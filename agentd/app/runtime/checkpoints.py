"""联合检查点提交日志；不调用restic，不重放工具，不修改Task预算。"""
from __future__ import annotations

import copy
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .durable import DurableJournal, JournalCorrupt
from .session_refs import SessionReferences

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
    def __init__(self, path: Path, refs: SessionReferences):
        self._journal = DurableJournal(path)
        self._refs = refs
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._heads: dict[tuple[str, str], str] = {}
        self._boundaries: dict[tuple[str, str, str], str] = {}
        self._restores: dict[str, dict[str, Any]] = {}
        self._origins: dict[tuple[str, str], str] = {}
        try:
            for entry in self._journal.entries():
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
                return r
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
                return r
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
        """管理清理的引用输入；未提供主动解除引用或删除快照能力。"""
        with self._lock:
            protected: dict[str, list[str]] = {}
            for (session, branch), cp in self._heads.items():
                protected.setdefault(cp, []).append("branch-head:" + session + "/" + branch)
            for request in self._restores.values():
                protected.setdefault(request["sourceCheckpointId"], []).append("restore-source:" + request["restoreId"])
            return protected

    def close(self) -> None:
        self._journal.close()
