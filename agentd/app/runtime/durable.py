"""单写入者控制日志；只供可信 Runtime 保存经校验、脱敏的控制状态。"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JournalError(RuntimeError):
    pass


class JournalInUse(JournalError):
    pass


class JournalCorrupt(JournalError):
    pass


def _encode(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


class DurableJournal:
    """持有文件锁直到 close；完整坏行拒绝启动，只有断尾可忽略和修复。

    checksum 用于检测意外损坏，不是对恶意管理员的防篡改认证。
    数据必须由调用方选取字段并脱敏，不能直接传入凭据或原始工具请求。
    """

    def __init__(self, path: Path, *, max_record_bytes: int = 2 << 20, read_only: bool = False) -> None:
        self.path = Path(path)
        self._mutex = threading.RLock()
        self._limit = max_record_bytes
        self._fd = -1
        self._poisoned = False
        self._read_only = read_only
        if not read_only:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
        else:
            info = self.path.parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise JournalError("read-only control directory requires private permissions")
        try:
            self._fd = os.open(
                self.path, (os.O_RDONLY if read_only else os.O_RDWR | os.O_CREAT) | os.O_NOFOLLOW, 0o600,
            )
            if not stat.S_ISREG(os.fstat(self._fd).st_mode):
                raise JournalError("control journal must be a regular file")
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JournalInUse("control journal already has an active owner") from None
            if not read_only:
                os.fchmod(self._fd, 0o600)
            # Windows 挂载盘可能接受 chmod 却不实现权限；不能伪报私有控制目录。
            if stat.S_IMODE(os.fstat(self._fd).st_mode) & 0o077:
                raise JournalError("control journal requires private Linux file permissions")
            with os.fdopen(os.dup(self._fd), "rb") as handle:
                handle.seek(0)
                self._records, self._valid_end = self._decode(handle)
            if not read_only:
                parent_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
        except BaseException:
            self.close()
            raise

    def _decode(self, handle: Any) -> tuple[list[dict[str, Any]], int]:
        records: list[dict[str, Any]] = []
        valid_end = 0
        while line := handle.readline(self._limit + 1):
            if len(line) > self._limit:
                raise JournalCorrupt("control journal record exceeds limit")
            if not line.endswith(b"\n"):
                break
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError
                checksum = record.pop("sha256")
                if checksum != hashlib.sha256(_encode(record)).hexdigest():
                    raise ValueError
                if (
                    set(record) != {"seq", "type", "timestamp", "data"}
                    or type(record["seq"]) is not int
                    or record["seq"] != len(records) + 1
                    or not isinstance(record["type"], str)
                    or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", record["type"])
                    or not isinstance(record["timestamp"], str)
                    or not isinstance(record["data"], dict)
                ):
                    raise ValueError
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise JournalCorrupt("invalid complete control journal record") from None
            records.append(record)
            valid_end += len(line)
        return records, valid_end

    def entries(self) -> list[dict[str, Any]]:
        with self._mutex:
            return copy.deepcopy(self._records)

    def append(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._mutex:
            if self._read_only:
                raise JournalError("read-only control journal cannot append")
            if self._fd < 0 or self._poisoned:
                raise JournalError("control journal is closed or requires recovery")
            if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", kind):
                raise ValueError("invalid control event type")
            if not isinstance(data, dict):
                raise ValueError("control event data must be an object")
            record = {
                "seq": len(self._records) + 1,
                "type": kind,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": data,
            }
            checksum = hashlib.sha256(_encode(record)).hexdigest()
            line = _encode({**record, "sha256": checksum}) + b"\n"
            if len(line) > self._limit:
                raise ValueError("control event exceeds record limit")
            # 序列化后再复制，调用方后续修改字典不会改变内存投影。
            frozen = json.loads(_encode(record))
            try:
                os.ftruncate(self._fd, self._valid_end)
                os.lseek(self._fd, self._valid_end, os.SEEK_SET)
                pending = memoryview(line)
                while pending:
                    written = os.write(self._fd, pending)
                    if written <= 0:
                        raise OSError("short control journal write")
                    pending = pending[written:]
                os.fsync(self._fd)
            except BaseException:
                self._poisoned = True
                raise
            self._valid_end += len(line)
            self._records.append(frozen)
            return copy.deepcopy(frozen)

    def close(self) -> None:
        with self._mutex:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1

    def __enter__(self) -> DurableJournal:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
