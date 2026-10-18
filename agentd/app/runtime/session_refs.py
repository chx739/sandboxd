"""联合检查点的不可变Session父链副本；控制文件留在沙箱外。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from uuid import uuid4
from typing import Any

from .durable import _encode, JournalCorrupt
from .session import SessionJournal, _branchable_flags, _SESSION_ID, _NODE_ID
from ..models import AlertEvent

MAX_PREFIX_BYTES = 2 << 20
MAX_PREFIX_NODES = 2048
_HASH = re.compile(r"[a-f0-9]{64}\Z")


class SessionReferences:
    def __init__(self, control_directory: Path):
        self.directory = Path(control_directory) / "session-refs"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Session引用需要原生Linux私有目录")

    @staticmethod
    def _validate_prefix(value: dict[str, Any]) -> None:
        if (not isinstance(value, dict)
                or set(value) != {"schemaVersion", "sessionId", "nodeId", "alert", "nodes"}
                or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
                or not isinstance(value["sessionId"], str) or not _SESSION_ID.fullmatch(value["sessionId"])
                or not isinstance(value["nodeId"], str) or not _NODE_ID.fullmatch(value["nodeId"])):
            raise JournalCorrupt("invalid immutable Session prefix")
        AlertEvent.model_validate(value["alert"])
        nodes = value["nodes"]
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_PREFIX_NODES:
            raise JournalCorrupt("Session prefix node budget exceeded")
        parent = None
        seen: set[str] = set()
        messages = []
        for node in nodes:
            if (not isinstance(node, dict) or not {"nodeId", "parentId", "message"} <= node.keys()
                    or node.keys() - {"nodeId", "parentId", "message", "taskId", "attemptId", "timestamp"}
                    or not isinstance(node["nodeId"], str) or not _NODE_ID.fullmatch(node["nodeId"])
                    or node["nodeId"] in seen or node["parentId"] != parent
                    or not isinstance(node["message"], dict)):
                raise JournalCorrupt("invalid Session reference parent chain")
            seen.add(node["nodeId"])
            parent = node["nodeId"]
            messages.append(node["message"])
        if parent != value["nodeId"] or not _branchable_flags(messages)[-1]:
            raise JournalCorrupt("Session reference ends inside an incomplete turn")

    @staticmethod
    def validate_reference(ref: dict[str, Any]) -> None:
        if (not isinstance(ref, dict) or set(ref) != {"schemaVersion", "path", "sha256", "bytes", "sessionId", "nodeId"}
                or type(ref["schemaVersion"]) is not int or ref["schemaVersion"] != 1
                or not isinstance(ref["sha256"], str) or not _HASH.fullmatch(ref["sha256"])
                or ref["path"] != "session-refs/" + ref["sha256"] + ".json"
                or type(ref["bytes"]) is not int or not 1 <= ref["bytes"] <= MAX_PREFIX_BYTES
                or not isinstance(ref["sessionId"], str) or not _SESSION_ID.fullmatch(ref["sessionId"])
                or not isinstance(ref["nodeId"], str) or not _NODE_ID.fullmatch(ref["nodeId"])):
            raise JournalCorrupt("invalid Session reference locator")

    async def capture(self, session: SessionJournal, node_id: str) -> dict[str, Any]:
        value = await session.checkpoint_prefix(node_id)
        self._validate_prefix(value)
        raw = _encode(value)
        if len(raw) > MAX_PREFIX_BYTES:
            raise ValueError("Session checkpoint prefix exceeds byte budget")
        digest = hashlib.sha256(raw).hexdigest()
        ref = {"schemaVersion": 1, "path": "session-refs/" + digest + ".json",
               "sha256": digest, "bytes": len(raw), "sessionId": session.session_id, "nodeId": node_id}
        target = self.directory / (digest + ".json")
        temporary = self.directory / (".pending-" + uuid4().hex)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                target.lstat()
            except FileNotFoundError:
                # 当前部署由单写入者发布控制文件。完整临时文件原子rename后
                # 再fsync目录；不用link，避免崩溃留下第二个硬链接导致引用拒读。
                os.replace(temporary, target)
            else:
                # 已存在的内容寻址对象须校验，不能用重新生成的副本掩盖损坏。
                self.read(ref)
        finally:
            temporary.unlink(missing_ok=True)
        directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self.read(ref)
        return ref

    def read(self, ref: dict[str, Any]) -> dict[str, Any]:
        self.validate_reference(ref)
        target = self.directory / (ref["sha256"] + ".json")
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size != ref["bytes"]):
                raise JournalCorrupt("Session reference file identity/permissions mismatch")
            raw = handle.read(MAX_PREFIX_BYTES + 1)
        if len(raw) != ref["bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise JournalCorrupt("Session reference checksum mismatch")
        value = json.loads(raw)
        self._validate_prefix(value)
        if value["sessionId"] != ref["sessionId"] or value["nodeId"] != ref["nodeId"]:
            raise JournalCorrupt("Session reference scope mismatch")
        return value
