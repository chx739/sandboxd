"""联合检查点提交日志；不调用restic，不重放工具，不修改Task预算。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .durable import DurableJournal, JournalCorrupt
from .session_refs import SessionReferences
from .maintenance import MaintenanceControl

_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,95}\Z")
_CP = re.compile(r"cp-[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_IMAGE_REF = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@+-]{0,511}\Z")
_IMAGE_ID = re.compile(r"(?:sha256:[a-f0-9]{64}|[a-zA-Z0-9][a-zA-Z0-9._:/@+-]{0,430}@sha256:[a-f0-9]{64})\Z")
_IDENTITY = {"sessionId", "branchId", "sessionNodeId", "workspaceId", "taskId", "attemptId",
             "sandboxId", "podUid", "generation", "imageRef", "imageId", "sessionRef"}
_DATA = {"repositoryId", "repositoryCryptoId", "snapshotId", "manifestSha256"}
_STATE = {"schemaVersion", "checkpointId", "parentCheckpointId", "identity", "status", "data",
          "revision", "createdAt", "committedAt", "failure"}
_FAILURES = {"backup_failed", "cancelled", "deadline", "conflict", "session_invalid"}
_RESTORE = {"restoreId", "sourceCheckpointId", "sessionId", "branchId", "workspaceId",
            "taskId", "status", "createdAt", "failure"}


class CheckpointConflict(RuntimeError):
    pass


def _time(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("checkpoint time needs timezone")
    return stamp.astimezone(timezone.utc)


class CheckpointLedger:
    """独立单写入日志；COMMITTED这一条记录同时表达head/revision前移。

    SessionReferences保存不可变可读父链，日志只保存定位与校验信息。
    commit须由协调器在Task取消锁/owner校验内调用，本模块不是跨服务锁。
    """
    def __init__(self, path: Path, refs: SessionReferences, *, read_only: bool = False):
        self._journal = DurableJournal(path, read_only=read_only)
        self._refs = refs
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._heads: dict[tuple[str, str], str] = {}
        self._boundaries: dict[tuple[str, str, str], str] = {}
        self._restores: dict[str, dict[str, Any]] = {}
        self._origins: dict[tuple[str, str], str] = {}
        self._pins: set[str] = set()
        self._forgets: dict[str, dict[str, Any]] = {}
        self.maintenance = MaintenanceControl(self)
        try:
            for entry in self._journal.entries():
                if entry["type"] in {"repository.binding", "cleanup.execution"}:
                    self.maintenance.replay(entry["type"], entry["data"])
                    continue
                if entry["type"] in {"checkpoint.pin", "checkpoint.forget"}:
                    self._validate_retention(entry["type"], entry["data"])
                    self._project_retention(entry["type"], entry["data"])
                    continue
                if entry["type"] == "checkpoint.restore":
                    self._validate_restore(entry["data"])
                    self._project_restore(entry["data"])
                    continue
                if entry["type"] != "checkpoint.state":
                    raise JournalCorrupt("unknown checkpoint event")
                self._validate(entry["data"])
                self._project(entry["data"])
        except BaseException:
            self._journal.close()
            raise

    @staticmethod
    def _branch(record: dict[str, Any]) -> tuple[str, str]:
        i = record["identity"]
        return i["sessionId"], i["branchId"]

    def _validate(self, r: dict[str, Any]) -> None:
        if (not isinstance(r, dict) or set(r) != _STATE or type(r["schemaVersion"]) is not int
                or r["schemaVersion"] != 1 or not isinstance(r["checkpointId"], str)
                or not _CP.fullmatch(r["checkpointId"])):
            raise JournalCorrupt("invalid checkpoint identity/schema")
        i = r["identity"]
        if not isinstance(i, dict) or set(i) != _IDENTITY:
            raise JournalCorrupt("invalid checkpoint binding fields")
        for key in _IDENTITY - {"generation", "sessionRef", "imageRef", "imageId"}:
            if not isinstance(i[key], str) or not _ID.fullmatch(i[key]):
                raise JournalCorrupt("invalid checkpoint binding identity")
        if (type(i["generation"]) is not int or i["generation"] <= 0
                or any(not isinstance(i[k], str) or not 1 <= len(i[k]) <= 512 for k in ("imageRef", "imageId"))):
            raise JournalCorrupt("checkpoint missing bound generation/image identity")
        if not _IMAGE_REF.fullmatch(i["imageRef"]) or not _IMAGE_ID.fullmatch(i["imageId"]):
            raise JournalCorrupt("checkpoint requires an immutable runtime image identity")
        for key, prefix in (("workspaceId", "workspace"), ("branchId", "branch"), ("attemptId", "attempt")):
            if not re.fullmatch(prefix + r"-[a-f0-9]{32}", i[key]):
                raise JournalCorrupt("invalid checkpoint scoped identity")
        SessionReferences.validate_reference(i["sessionRef"])
        if i["sessionId"] != i["sessionRef"]["sessionId"] or i["sessionNodeId"] != i["sessionRef"]["nodeId"]:
            raise JournalCorrupt("checkpoint Session scope mismatch")
        _time(r["createdAt"])
        if r["parentCheckpointId"] is not None and (not isinstance(r["parentCheckpointId"], str) or not _CP.fullmatch(r["parentCheckpointId"])):
            raise JournalCorrupt("invalid checkpoint parent")
        if r["data"] is not None:
            data = r["data"]
            if (not isinstance(data, dict) or set(data) != _DATA
                    or not isinstance(data["repositoryId"], str) or not _ID.fullmatch(data["repositoryId"])
                    or any(not isinstance(data[k], str) or not _HASH.fullmatch(data[k]) for k in _DATA - {"repositoryId"})):
                raise JournalCorrupt("checkpoint requires full repository/snapshot/manifest identities")
            if self.maintenance._binding is not None and data["repositoryCryptoId"] != self.maintenance.binding()["owner"]["repositoryCryptoId"]:
                raise JournalCorrupt("checkpoint belongs to another bound repository")
            if any((data["repositoryCryptoId"], data["snapshotId"]) == (g["repositoryCryptoId"], g["snapshotId"])
                   for g in self._forgets.values()):
                raise JournalCorrupt("checkpoint reuses a pending/forgotten snapshot")
        if r["status"] not in {"PREPARING", "DATA_READY", "COMMITTED", "FAILED"}:
            raise JournalCorrupt("invalid checkpoint state")
        if r["status"] == "PREPARING" and r["data"] is not None:
            raise JournalCorrupt("PREPARING already contains a snapshot")
        if r["status"] in {"DATA_READY", "COMMITTED"} and r["data"] is None:
            raise JournalCorrupt("checkpoint data missing")
        if r["status"] == "COMMITTED":
            if type(r["revision"]) is not int or r["revision"] < 1 or r["committedAt"] is None:
                raise JournalCorrupt("invalid committed revision/time")
            if _time(r["committedAt"]) < _time(r["createdAt"]):
                raise JournalCorrupt("checkpoint clock moved backwards")
        elif r["revision"] is not None or r["committedAt"] is not None:
            raise JournalCorrupt("uncommitted checkpoint advances head")
        if (r["status"] == "FAILED" and r["failure"] not in _FAILURES) or (r["status"] != "FAILED" and r["failure"] is not None):
            raise JournalCorrupt("invalid checkpoint failure reason")
        previous = self._records.get(r["checkpointId"])
        if previous is None:
            if self.maintenance.pending():
                raise JournalCorrupt("new checkpoint during pending maintenance")
            if any(g["status"] == "PREPARED" for g in self._forgets.values()):
                raise JournalCorrupt("new checkpoint during pending cleanup")
            if r["status"] != "PREPARING" or self._parent(self._branch(r)) != r["parentCheckpointId"]:
                raise JournalCorrupt("checkpoint must start at current committed head")
            if self._branch(r) not in self._heads and self._branch(r) in self._origins:
                origin = self._restores[self._origins[self._branch(r)]]
                source = self._records[origin["sourceCheckpointId"]]
                if i["workspaceId"] != origin["workspaceId"] or i["taskId"] != origin["taskId"]:
                    raise JournalCorrupt("first restored checkpoint changed target binding")
                prefix = self._refs.read(i["sessionRef"])["nodes"]
                parent_prefix = self._refs.read(source["identity"]["sessionRef"])["nodes"]
                if prefix[:len(parent_prefix)] != parent_prefix:
                    raise JournalCorrupt("restored checkpoint does not extend exact source Session")
            boundary = (i["taskId"], i["attemptId"], i["sessionNodeId"])
            if boundary in self._boundaries:
                raise JournalCorrupt("checkpoint boundary already has an idempotency identity")
        else:
            if any(r[k] != previous[k] for k in ("identity", "parentCheckpointId", "createdAt")):
                raise JournalCorrupt("checkpoint binding changed during retry")
            allowed = {"PREPARING": {"DATA_READY", "FAILED"}, "DATA_READY": {"COMMITTED", "FAILED"}}
            if r["status"] not in allowed.get(previous["status"], set()):
                raise JournalCorrupt("invalid checkpoint transition")
            if previous["data"] is not None and previous["data"] != r["data"]:
                raise JournalCorrupt("checkpoint data identity changed")
        if r["status"] == "COMMITTED":
            head = self._heads.get(self._branch(r))
            if self._parent(self._branch(r)) != r["parentCheckpointId"] or r["revision"] != (self._records[head]["revision"] if head else 0) + 1:
                raise JournalCorrupt("checkpoint head/revision compare-and-swap failed")

    def _parent(self, branch: tuple[str, str]) -> str | None:
        head = self._heads.get(branch)
        origin = self._origins.get(branch)
        return head or (self._restores[origin]["sourceCheckpointId"] if origin else None)

    def branch_origin(self, session_id: str, branch_id: str) -> str | None:
        with self._lock:
            origin = self._origins.get((session_id, branch_id))
            return self._restores[origin]["sourceCheckpointId"] if origin else None

    def committed_at_node(self, session_id: str, node_id: str) -> list[str]:
        with self._lock:
            return [cp for cp, record in self._records.items() if record["status"] == "COMMITTED"
                    and record["identity"]["sessionId"] == session_id
                    and record["identity"]["sessionNodeId"] == node_id
                    and self.file_availability(cp) == "available"]

    def _project(self, state: dict[str, Any]) -> None:
        r = copy.deepcopy(state)
        self._records[r["checkpointId"]] = r
        i = r["identity"]
        self._boundaries[(i["taskId"], i["attemptId"], i["sessionNodeId"])] = r["checkpointId"]
        if r["status"] == "COMMITTED":
            self._heads[self._branch(r)] = r["checkpointId"]

    def _save(self, state: dict[str, Any]) -> dict[str, Any]:
        self._validate(state)
        self._journal.append("checkpoint.state", state)
        self._project(state)
        return copy.deepcopy(state)

    def get(self, checkpoint_id: str) -> dict[str, Any]:
        with self._lock:
            if checkpoint_id not in self._records:
                raise CheckpointConflict("unknown checkpoint")
            return copy.deepcopy(self._records[checkpoint_id])

    def head(self, session_id: str, branch_id: str) -> dict[str, Any] | None:
        with self._lock:
            key = self._heads.get((session_id, branch_id))
            return self.get(key) if key else None

    def boundary(self, task_id: str, attempt_id: str, node_id: str) -> dict[str, Any] | None:
        """崩溃后按完整边界找回首次生成的UUID，不能重新分配保存请求。"""
        with self._lock:
            key = self._boundaries.get((task_id, attempt_id, node_id))
            return self.get(key) if key else None

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(r) for r in self._records.values() if r["status"] in {"PREPARING", "DATA_READY"}]

    def prepare(self, checkpoint_id: str, identity: dict[str, Any], expected_head: str | None) -> dict[str, Any]:
        with self._lock:
            if checkpoint_id in self._records:
                old = self.get(checkpoint_id)
                if old["identity"] != identity or old["parentCheckpointId"] != expected_head:
                    raise CheckpointConflict("checkpoint retry changed binding")
                return old
            if self.maintenance.pending() or any(r["status"] == "PREPARED" for r in self._forgets.values()):
                raise CheckpointConflict("repository cleanup pending; reconcile before new checkpoint")
            if self._parent((identity["sessionId"], identity["branchId"])) != expected_head:
                raise CheckpointConflict("branch head changed")
            self._refs.read(identity["sessionRef"])
            return self._save({"schemaVersion": 1, "checkpointId": checkpoint_id, "parentCheckpointId": expected_head,
                               "identity": copy.deepcopy(identity), "status": "PREPARING", "data": None,
                               "revision": None, "createdAt": datetime.now(timezone.utc).isoformat(),
                               "committedAt": None, "failure": None})

    def data_ready(self, checkpoint_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            r = self.get(checkpoint_id)
            if r["status"] in {"DATA_READY", "COMMITTED"} and r["data"] == data:
                return self.restore_point(checkpoint_id)[0] if r["status"] == "COMMITTED" else r
            if r["status"] != "PREPARING":
                raise CheckpointConflict("checkpoint cannot accept another snapshot")
            r.update(status="DATA_READY", data=copy.deepcopy(data))
            return self._save(r)

    def commit(self, checkpoint_id: str) -> dict[str, Any]:
        """协调器须在共享取消锁内先核对Task/Go owner；这里原子提交本地head。"""
        with self._lock:
            r = self.get(checkpoint_id)
            self._refs.read(r["identity"]["sessionRef"])
            if r["status"] == "COMMITTED":
                return self.restore_point(checkpoint_id)[0]
            if r["status"] != "DATA_READY" or self._parent(self._branch(r)) != r["parentCheckpointId"]:
                raise CheckpointConflict("checkpoint is not data-ready or head changed")
            head = self.head(*self._branch(r))
            r.update(status="COMMITTED", revision=(head["revision"] if head else 0) + 1,
                     committedAt=datetime.now(timezone.utc).isoformat())
            return self._save(r)

    def fail(self, checkpoint_id: str, reason: str) -> dict[str, Any]:
        with self._lock:
            if reason not in _FAILURES:
                raise ValueError("failure reason must be a fixed non-secret code")
            r = self.get(checkpoint_id)
            if r["status"] == "FAILED" and r["failure"] == reason:
                return r
            if r["status"] not in {"PREPARING", "DATA_READY"}:
                raise CheckpointConflict("terminal checkpoint cannot change")
            r.update(status="FAILED", failure=reason)
            return self._save(r)

    def restore_point(self, checkpoint_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock:
            r = self.get(checkpoint_id)
            if r["status"] != "COMMITTED":
                raise CheckpointConflict("checkpoint is not a committed restore point")
            if self.file_availability(checkpoint_id) != "available":
                raise CheckpointConflict("checkpoint file snapshot is pending forget or forgotten")
            return r, self._refs.read(r["identity"]["sessionRef"])

    def _validate_restore(self, record: dict[str, Any]) -> None:
        if not isinstance(record, dict) or set(record) != _RESTORE:
            raise JournalCorrupt("invalid restore record fields")
        for key, prefix in (("restoreId", "restore"), ("branchId", "branch"), ("workspaceId", "workspace")):
            if not isinstance(record[key], str) or not re.fullmatch(prefix + r"-[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}", record[key]):
                raise JournalCorrupt("restore target needs fixed UUIDv4 identities")
        for key in ("sessionId", "taskId", "sourceCheckpointId"):
            if not isinstance(record[key], str) or not _ID.fullmatch(record[key]):
                raise JournalCorrupt("invalid restore scope")
        _time(record["createdAt"])
        source = self._records.get(record["sourceCheckpointId"])
        if (source is None or source["status"] != "COMMITTED"
                or source["identity"]["sessionId"] != record["sessionId"]
                or source["identity"]["branchId"] == record["branchId"]
                or source["identity"]["workspaceId"] == record["workspaceId"]):
            raise JournalCorrupt("restore requires same Session and independent target")
        if record["status"] not in {"RESERVED", "FILES_READY", "ATTACHED", "FAILED"}:
            raise JournalCorrupt("invalid restore state")
        if ((record["status"] == "FAILED" and record["failure"] not in _FAILURES)
                or (record["status"] != "FAILED" and record["failure"] is not None)):
            raise JournalCorrupt("invalid restore failure code")
        old = self._restores.get(record["restoreId"])
        if old is None:
            if self.maintenance.pending():
                raise JournalCorrupt("new restore during pending maintenance")
            if any(g["status"] == "PREPARED" for g in self._forgets.values()):
                raise JournalCorrupt("new restore during pending cleanup")
            if self.file_availability(record["sourceCheckpointId"]) != "available":
                raise JournalCorrupt("restore source snapshot is unavailable")
            if record["status"] != "RESERVED":
                raise JournalCorrupt("restore must reserve source before execution")
            for other in self._restores.values():
                if any(record[key] == other[key] for key in ("branchId", "workspaceId", "taskId")):
                    raise JournalCorrupt("restore target identity is already reserved")
            for checkpoint in self._records.values():
                if any(record[key] == checkpoint["identity"][key] for key in ("branchId", "workspaceId", "taskId")):
                    raise JournalCorrupt("restore cannot reuse an existing target")
        else:
            if any(record[key] != old[key] for key in _RESTORE - {"status", "failure"}):
                raise JournalCorrupt("restore retry changed immutable binding")
            transitions = {"RESERVED": {"FILES_READY", "FAILED"}, "FILES_READY": {"ATTACHED", "FAILED"}}
            if record["status"] not in transitions.get(old["status"], set()):
                raise JournalCorrupt("invalid restore transition")

    def _project_restore(self, record: dict[str, Any]) -> None:
        self._restores[record["restoreId"]] = copy.deepcopy(record)
        if record["status"] == "ATTACHED":
            self._origins[(record["sessionId"], record["branchId"])] = record["restoreId"]

    def _save_restore(self, record: dict[str, Any]) -> dict[str, Any]:
        self._validate_restore(record)
        self._journal.append("checkpoint.restore", record)
        self._project_restore(record)
        return copy.deepcopy(record)

    def prepare_restore(self, restore_id: str, checkpoint_id: str, *, session_id: str,
                        branch_id: str, workspace_id: str, task_id: str) -> dict[str, Any]:
        """先fsync固定来源和目标，调用方随后才能发起任何远程恢复动作。"""
        with self._lock:
            source, _ = self.restore_point(checkpoint_id)
            binding = {"restoreId": restore_id, "sourceCheckpointId": source["checkpointId"],
                       "sessionId": session_id, "branchId": branch_id,
                       "workspaceId": workspace_id, "taskId": task_id}
            old = self._restores.get(restore_id)
            if old is not None:
                if any(old[key] != value for key, value in binding.items()):
                    raise CheckpointConflict("restore retry changed binding")
                return copy.deepcopy(old)
            return self._save_restore({**binding, "status": "RESERVED", "failure": None,
                                       "createdAt": datetime.now(timezone.utc).isoformat()})

    def restore_request(self, restore_id: str) -> dict[str, Any]:
        with self._lock:
            if restore_id not in self._restores:
                raise CheckpointConflict("unknown restore request")
            return copy.deepcopy(self._restores[restore_id])

    def restore_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(self._restores.values()))

    def advance_restore(self, restore_id: str, status: str, failure: str | None = None) -> dict[str, Any]:
        """仅协调器使用：FILES_READY需可信恢复证明，ATTACHED需持久Task绑定。

        本日志不能替代Go核验或Task提交；失败也保留来源引用，不能立即forget。
        """
        with self._lock:
            record = self.restore_request(restore_id)
            if record["status"] == status and record["failure"] == failure:
                return record
            if status in {"FILES_READY", "ATTACHED"}:
                self.restore_point(record["sourceCheckpointId"])
            record.update(status=status, failure=failure)
            return self._save_restore(record)

    def protected_checkpoints(self) -> dict[str, list[str]]:
        """按分支头、保留恢复源及显式pin生成引用；失败恢复也保持来源。"""
        with self._lock:
            protected: dict[str, list[str]] = {}
            for (session, branch), cp in self._heads.items():
                protected.setdefault(cp, []).append("branch-head:" + session + "/" + branch)
            for request in self._restores.values():
                protected.setdefault(request["sourceCheckpointId"], []).append("restore-source:" + request["restoreId"])
            for cp in sorted(self._pins):
                protected.setdefault(cp, []).append("explicit-pin")
            return protected

    def file_availability(self, checkpoint_id: str) -> str:
        """历史COMMITTED日志保留；文件恢复资格由独立遗忘投影决定。"""
        with self._lock:
            record = self.get(checkpoint_id)
            data = record["data"]
            if data:
                for operation in self._forgets.values():
                    if (data["repositoryCryptoId"], data["snapshotId"]) == (operation["repositoryCryptoId"], operation["snapshotId"]):
                        return "forget-pending" if operation["status"] == "PREPARED" else "forgotten"
            return "available" if record["status"] == "COMMITTED" else "uncommitted"

    def cleanup_preview(self) -> dict[str, Any]:
        """仅本控制账本已知引用；不授权删除未知快照/其他部署，也不执行后端操作。"""
        with self._lock:
            protected = self.protected_checkpoints()
            pending = sorted(r["checkpointId"] for r in self.pending())
            active_restores = sorted(r["restoreId"] for r in self._restores.values() if r["status"] in {"RESERVED", "FILES_READY"})
            pending_gc = sorted(r["gcId"] for r in self._forgets.values() if r["status"] == "PREPARED")
            groups: dict[tuple[str, str], dict[str, Any]] = {}
            for cp, record in self._records.items():
                data = record["data"]
                if data is None:
                    continue
                key = (data["repositoryCryptoId"], data["snapshotId"])
                group = groups.setdefault(key, {"repositoryCryptoId": key[0], "snapshotId": key[1],
                                               "checkpointIds": [], "repositoryIds": [], "reasons": []})
                group["checkpointIds"].append(cp)
                group["repositoryIds"].append(data["repositoryId"])
                group["reasons"].extend(cp + ":" + reason for reason in protected.get(cp, []))
                if record["status"] != "COMMITTED":
                    group["reasons"].append(cp + ":uncommitted-data")
                else:
                    try:
                        self._refs.read(record["identity"]["sessionRef"])
                    except (OSError, ValueError, JournalCorrupt):
                        group["reasons"].append(cp + ":session-reference-unreadable")
            for group in groups.values():
                ids = group["checkpointIds"]
                group["availability"] = self.file_availability(ids[0])
                group["checkpointIds"] = sorted(ids)
                group["repositoryIds"] = sorted(set(group["repositoryIds"]))
                if pending:
                    group["reasons"].append("pending-checkpoint")
                if active_restores:
                    group["reasons"].append("active-restore")
                if pending_gc:
                    group["reasons"].append("cleanup-in-progress")
                group["reasons"] = sorted(set(group["reasons"]))
                group["eligible"] = not group["reasons"] and group["availability"] == "available"
            view = {"schemaVersion": 1, "pendingCheckpointIds": pending,
                    "activeRestoreIds": active_restores, "pendingGcIds": pending_gc,
                    "snapshots": [groups[key] for key in sorted(groups)]}
            digest = hashlib.sha256(json.dumps(view, ensure_ascii=False, sort_keys=True,
                                              separators=(",", ":")).encode()).hexdigest()
            return {**view, "planSha256": digest}

    def _validate_retention(self, kind: str, record: dict[str, Any]) -> None:
        if kind == "checkpoint.pin":
            if (not isinstance(record, dict) or set(record) != {"checkpointId", "retained"}
                    or type(record["retained"]) is not bool or not isinstance(record["checkpointId"], str)
                    or record["checkpointId"] not in self._records
                    or self.file_availability(record["checkpointId"]) != "available"
                    or (record["checkpointId"] in self._pins) == record["retained"]):
                raise JournalCorrupt("invalid checkpoint pin transition")
            return
        fields = {"schemaVersion", "gcId", "repositoryCryptoId", "snapshotId", "checkpointIds", "planSha256", "status"}
        if (not isinstance(record, dict) or set(record) != fields or type(record["schemaVersion"]) is not int
                or record["schemaVersion"] != 1 or not isinstance(record["gcId"], str)
                or not re.fullmatch(r"gc-[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}", record["gcId"])
                or any(not isinstance(record[k], str) or not _HASH.fullmatch(record[k])
                       for k in ("repositoryCryptoId", "snapshotId", "planSha256"))):
            raise JournalCorrupt("invalid checkpoint forget identity")
        old = self._forgets.get(record["gcId"])
        if old is not None:
            if (any(record[k] != old[k] for k in fields - {"status"})
                    or old["status"] != "PREPARED" or record["status"] != "FORGOTTEN"):
                raise JournalCorrupt("invalid checkpoint forget transition")
            return
        if self.maintenance._binding is not None:
            execution = self.maintenance.get(record["gcId"])
            if (execution is None or execution["status"] != "PENDING" or execution["operation"] != "forget"
                    or execution["snapshotId"] != record["snapshotId"] or execution["planSha256"] != record["planSha256"]
                    or record["repositoryCryptoId"] != self.maintenance.binding()["owner"]["repositoryCryptoId"]):
                raise JournalCorrupt("owned forget requires its persisted management execution")
        view = self.cleanup_preview()
        target = next((r for r in view["snapshots"] if r["repositoryCryptoId"] == record["repositoryCryptoId"]
                       and r["snapshotId"] == record["snapshotId"]), None)
        if (record["status"] != "PREPARED" or record["planSha256"] != view["planSha256"] or target is None
                or not target["eligible"] or record["checkpointIds"] != target["checkpointIds"]
                or any(r["status"] == "PREPARED" for r in self._forgets.values())):
            raise JournalCorrupt("forget plan changed, protected snapshot or cleanup already pending")

    def _project_retention(self, kind: str, record: dict[str, Any]) -> None:
        if kind == "checkpoint.pin":
            if record["retained"]:
                self._pins.add(record["checkpointId"])
            else:
                self._pins.remove(record["checkpointId"])
        else:
            self._forgets[record["gcId"]] = copy.deepcopy(record)

    def _save_retention(self, kind: str, record: dict[str, Any]) -> dict[str, Any]:
        self._validate_retention(kind, record)
        self._journal.append(kind, record)
        self._project_retention(kind, record)
        return copy.deepcopy(record)

    def retain(self, checkpoint_id: str, retained: bool) -> None:
        with self._lock:
            if type(retained) is not bool or self.file_availability(checkpoint_id) != "available":
                raise CheckpointConflict("only an available committed checkpoint may be pinned")
            if (checkpoint_id in self._pins) != retained:
                self._save_retention("checkpoint.pin", {"checkpointId": checkpoint_id, "retained": retained})

    def prepare_forget(self, gc_id: str, repository_crypto_id: str, snapshot_id: str, plan_sha256: str) -> dict[str, Any]:
        """内部管理协调器：先使整个快照组不可恢复，再启动可信后端forget。

        未知后端结果保持PREPARED，不自动撤销或重新放行恢复。
        """
        with self._lock:
            old = self._forgets.get(gc_id)
            if old:
                if any(old[k] != v for k, v in {"repositoryCryptoId": repository_crypto_id,
                                               "snapshotId": snapshot_id, "planSha256": plan_sha256}.items()):
                    raise CheckpointConflict("forget retry changed immutable binding")
                return copy.deepcopy(old)
            view = self.cleanup_preview()
            target = next((r for r in view["snapshots"] if r["repositoryCryptoId"] == repository_crypto_id
                           and r["snapshotId"] == snapshot_id), None)
            if target is None or not target["eligible"] or view["planSha256"] != plan_sha256:
                raise CheckpointConflict("unknown/protected snapshot or stale cleanup preview")
            return self._save_retention("checkpoint.forget", {"schemaVersion": 1, "gcId": gc_id,
                "repositoryCryptoId": repository_crypto_id, "snapshotId": snapshot_id,
                "checkpointIds": target["checkpointIds"], "planSha256": plan_sha256, "status": "PREPARED"})

    def complete_forget(self, gc_id: str) -> dict[str, Any]:
        """仅可信协调器确认同仓库真实forget结果后使用；本方法本身不证明删除成功。"""
        with self._lock:
            if gc_id not in self._forgets:
                raise CheckpointConflict("unknown cleanup request")
            record = copy.deepcopy(self._forgets[gc_id])
            if record["status"] == "FORGOTTEN":
                return record
            record["status"] = "FORGOTTEN"
            return self._save_retention("checkpoint.forget", record)

    def forget_request(self, gc_id: str) -> dict[str, Any]:
        with self._lock:
            if gc_id not in self._forgets:
                raise CheckpointConflict("unknown cleanup request")
            return copy.deepcopy(self._forgets[gc_id])

    def forget_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(self._forgets.values()))

    def assert_maintenance_idle(self) -> None:
        with self._lock:
            if self.maintenance.pending() or any(r["status"] == "PREPARED" for r in self._forgets.values()):
                raise CheckpointConflict("repository maintenance pending; administrator must reconcile original request")

    def close(self) -> None:
        self._journal.close()
