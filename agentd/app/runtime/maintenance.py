"""同一检查点JSONL内的管理控制；不保存Operator Token，不调用后端。"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import stat

from .durable import JournalCorrupt, JournalError, _encode
from .recovery import new_id

CATALOGUE_ROOT = Path.home() / ".local/share/sandboxd/repository-controllers"
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_GC = re.compile(r"gc-[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}\Z")
_CONTROLLER = re.compile(r"controller-[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}\Z")
_JOB = re.compile(r"[a-z0-9][a-z0-9-]{0,45}\Z")


def _directory(path: Path) -> dict:
    info = path.lstat()
    if (not path.is_absolute() or path != path.resolve(strict=True) or not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid()):
        raise JournalError("management requires an existing private native control directory without symlinks")
    return {"directory": str(path), "device": info.st_dev, "inode": info.st_ino}


def _claim(binding: dict, *, create: bool = False) -> None:
    """单WSL部署目录登记；不能替代跨主机锁或恶意管理员防护。"""
    if create:
        CATALOGUE_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    _directory(CATALOGUE_ROOT)
    path = CATALOGUE_ROOT / (binding["owner"]["repositoryCryptoId"] + ".json")
    if create:
        temporary = CATALOGUE_ROOT / (".claim-" + new_id("owner"))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(_encode(binding) + b"\n"); handle.flush(); os.fsync(handle.fileno())
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                pass
            parent = os.open(CATALOGUE_ROOT, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            temporary.unlink()  # 仅清理本次独占新建的临时claim，不删除已有绑定。
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 4096):
            raise JournalError("repository directory claim is not a private regular file")
        if json.loads(handle.read(4097)) != binding:
            raise JournalError("repository is already bound to another control directory")


class MaintenanceControl:
    def __init__(self, ledger):
        self.ledger = ledger
        self._binding: dict | None = None
        self._runs: dict[str, dict] = {}
        self._jobs: set[str] = set()

    def binding(self) -> dict:
        with self.ledger._lock:
            if self._binding is None:
                raise JournalError("cleanup requires an explicitly owned new repository")
            if {k: self._binding[k] for k in ("directory", "device", "inode")} != _directory(self.ledger._journal.path.parent):
                raise JournalError("repository control directory identity changed")
            _claim(self._binding)
            return copy.deepcopy(self._binding)

    def pending(self) -> bool:
        with self.ledger._lock:
            return any(r["status"] == "PENDING" for r in self._runs.values())

    def get(self, gc_id: str) -> dict | None:
        with self.ledger._lock:
            return copy.deepcopy(self._runs.get(gc_id))

    def view(self) -> dict:
        with self.ledger._lock:
            return {"owner": copy.deepcopy(self._binding["owner"]) if self._binding else None,
                    "pendingExecutions": [copy.deepcopy(r) for _, r in sorted(self._runs.items()) if r["status"] == "PENDING"]}

    def _validate(self, kind: str, record: dict) -> None:
        if kind == "repository.binding":
            if (not isinstance(record, dict) or set(record) != {"schemaVersion", "owner", "directory", "device", "inode"}
                    or type(record["schemaVersion"]) is not int or record["schemaVersion"] != 1
                    or self._binding is not None or self.ledger._records or self.ledger._restores or self._runs):
                raise JournalCorrupt("repository binding must precede checkpoint history")
            owner = record["owner"]
            if (not isinstance(owner, dict) or set(owner) != {"controllerId", "repositoryCryptoId", "ownerSnapshotId"}
                    or not isinstance(owner["controllerId"], str) or not _CONTROLLER.fullmatch(owner["controllerId"])
                    or any(not isinstance(owner[k], str) or not _HASH.fullmatch(owner[k]) for k in ("repositoryCryptoId", "ownerSnapshotId"))
                    or any(type(record[k]) is not int for k in ("device", "inode"))
                    or {k: record[k] for k in ("directory", "device", "inode")} != _directory(self.ledger._journal.path.parent)):
                raise JournalCorrupt("invalid repository control binding")
            _claim(record)
            return
        fields = {"schemaVersion", "gcId", "operation", "snapshotId", "planSha256", "jobId", "previousJobId", "attempt", "status"}
        if (kind != "cleanup.execution" or not isinstance(record, dict) or set(record) != fields
                or type(record["schemaVersion"]) is not int or record["schemaVersion"] != 1
                or not isinstance(record["gcId"], str) or not _GC.fullmatch(record["gcId"])
                or not isinstance(record["jobId"], str) or not _JOB.fullmatch(record["jobId"])
                or not isinstance(record["planSha256"], str) or not _HASH.fullmatch(record["planSha256"])
                or type(record["attempt"]) is not int or record["attempt"] < 1
                or record["status"] not in {"PENDING", "DONE", "CANCELLED"}):
            raise JournalCorrupt("invalid cleanup execution identity")
        owner = self.binding()["owner"]
        if ((record["operation"] == "forget" and (not isinstance(record["snapshotId"], str) or not _HASH.fullmatch(record["snapshotId"]) or record["snapshotId"] == owner["ownerSnapshotId"]))
                or (record["operation"] == "prune" and record["snapshotId"] != "") or record["operation"] not in {"forget", "prune"}):
            raise JournalCorrupt("invalid fixed cleanup operation/target")
        old = self._runs.get(record["gcId"])
        if old is None:
            view = self.ledger.cleanup_preview()
            target = next((r for r in view["snapshots"] if r["repositoryCryptoId"] == owner["repositoryCryptoId"] and r["snapshotId"] == record["snapshotId"]), None)
            if (record["status"] != "PENDING" or record["attempt"] != 1 or record["previousJobId"] is not None
                    or record["jobId"] in self._jobs or self.pending() or record["planSha256"] != view["planSha256"]
                    or view["pendingCheckpointIds"] or view["activeRestoreIds"] or view["pendingGcIds"]
                    or (record["operation"] == "forget" and (target is None or not target["eligible"]))):
                raise JournalCorrupt("stale/protected cleanup plan or another maintenance pending")
            return
        if any(record[k] != old[k] for k in ("schemaVersion", "gcId", "operation", "snapshotId", "planSha256")) or old["status"] != "PENDING":
            raise JournalCorrupt("cleanup binding changed or terminal request reused")
        if record["status"] == "PENDING":
            if (record["attempt"] != old["attempt"] + 1 or record["previousJobId"] != old["jobId"] or record["jobId"] in self._jobs):
                raise JournalCorrupt("invalid explicit cleanup retry")
        elif any(record[k] != old[k] for k in ("jobId", "previousJobId", "attempt")):
            raise JournalCorrupt("cleanup completion changed attempt")
        forget = self.ledger._forgets.get(record["gcId"])
        if record["status"] == "DONE" and record["operation"] == "forget" and (forget is None or forget["status"] != "FORGOTTEN"):
            raise JournalCorrupt("cleanup completed before FORGOTTEN")
        if record["status"] == "CANCELLED" and forget is not None:
            raise JournalCorrupt("cannot cancel a durable forget intent")

    def replay(self, kind: str, record: dict) -> None:
        self._validate(kind, record)
        if kind == "repository.binding":
            self._binding = copy.deepcopy(record)
        else:
            self._runs[record["gcId"]] = copy.deepcopy(record); self._jobs.add(record["jobId"])

    def _save(self, kind: str, record: dict) -> dict:
        self._validate(kind, record)
        self.ledger._journal.append(kind, record)
        # 同一短锁下已校验并落盘，不再做可能失败的外部目录访问。
        if kind == "repository.binding":
            self._binding = copy.deepcopy(record)
        else:
            self._runs[record["gcId"]] = copy.deepcopy(record); self._jobs.add(record["jobId"])
        return copy.deepcopy(record)

    def bind(self, owner: dict) -> dict:
        with self.ledger._lock:
            if self._binding is not None:
                binding = self.binding()
                if binding["owner"] != owner:
                    raise JournalError("repository binding changed")
                return binding
            if self.ledger._journal.entries():
                raise JournalError("existing checkpoint history cannot be adopted")
            if any(self.ledger._refs.directory.iterdir()):
                raise JournalError("existing Session references cannot be adopted")
            # 先验证owner，再登记本机排他的持久目录claim；崩溃后同目录可接续。
            if (not isinstance(owner, dict) or set(owner) != {"controllerId", "repositoryCryptoId", "ownerSnapshotId"}
                    or not isinstance(owner["controllerId"], str) or not _CONTROLLER.fullmatch(owner["controllerId"])
                    or any(not isinstance(owner[k], str) or not _HASH.fullmatch(owner[k]) for k in ("repositoryCryptoId", "ownerSnapshotId"))):
                raise JournalError("invalid repository ownership identity")
            binding = {"schemaVersion": 1, "owner": copy.deepcopy(owner), **_directory(self.ledger._journal.path.parent)}
            _claim(binding, create=True)
            return self._save("repository.binding", binding)

    def plan(self, gc_id: str, operation: str, snapshot_id: str, plan_sha256: str) -> dict:
        with self.ledger._lock:
            old = self.get(gc_id)
            if old is not None:
                if any(old[k] != v for k, v in {"operation": operation, "snapshotId": snapshot_id, "planSha256": plan_sha256}.items()):
                    raise JournalError("cleanup retry changed target or preview")
                return old
            return self._save("cleanup.execution", {"schemaVersion": 1, "gcId": gc_id, "operation": operation,
                "snapshotId": snapshot_id, "planSha256": plan_sha256, "jobId": "cleanup-" + gc_id[3:],
                "previousJobId": None, "attempt": 1, "status": "PENDING"})

    def retry(self, gc_id: str) -> dict:
        with self.ledger._lock:
            record = self.get(gc_id)
            if record is None:
                raise JournalError("unknown cleanup request")
            record.update(previousJobId=record["jobId"], jobId=new_id("gcjob"), attempt=record["attempt"] + 1)
            return self._save("cleanup.execution", record)

    def complete(self, gc_id: str, *, cancelled: bool = False) -> dict:
        with self.ledger._lock:
            record = self.get(gc_id)
            if record is None:
                raise JournalError("unknown cleanup request")
            status = "CANCELLED" if cancelled else "DONE"
            if record["status"] == status:
                return record
            record["status"] = status
            return self._save("cleanup.execution", record)
