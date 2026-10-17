"""显式旧工作区映射。管理命令持有Task账本锁；不向LLM暴露导入能力。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from .durable import DurableJournal, JournalCorrupt


def manifest_hash(files: list[dict]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def checked_path(path: str) -> None:
    if (not isinstance(path, str) or not path or len(path.encode()) > 512 or len(path.split("/")) > 8
            or any(p in {"", ".", ".."} for p in path.split("/")) or "\\" in path or "\x00" in path
            or any(p.startswith(".sandboxd-") for p in path.split("/"))):
        raise ValueError("unsupported import path")


class WorkspaceImports:
    def __init__(self, path: Path):
        self.journal = DurableJournal(path)
        self.records: dict[str, dict] = {}
        try:
            for event in self.journal.entries():
                if event["type"] != "workspace.import":
                    raise JournalCorrupt("unsupported workspace import record")
                self._check(event["data"])
                self.records[event["data"]["sessionId"]] = event["data"]
        except BaseException:
            self.close()
            raise

    def _check(self, data: dict):
        try:
            if set(data) != {"importId", "sessionId", "sourceTaskId", "nodeId", "branchId", "workspaceId",
                             "sourceSessionSha256", "sessionSha256", "manifestSha256", "files", "attempts", "status"}:
                raise ValueError("invalid import fields")
            for key, prefix, length in (("importId", "import", 32), ("sessionId", "session", 16),
                                        ("sourceTaskId", "task", 16), ("nodeId", "node", 16),
                                        ("branchId", "branch", 32), ("workspaceId", "workspace", 32)):
                if not re.fullmatch(prefix + "-[a-f0-9]{" + str(length) + "}", data[key]):
                    raise ValueError("invalid import identity")
            for key in ("sourceSessionSha256", "sessionSha256", "manifestSha256"):
                if not re.fullmatch("[a-f0-9]{64}", data[key]):
                    raise ValueError("invalid import hash")
            files, attempts = data["files"], data["attempts"]
            if not isinstance(files, list) or len(files) > 100 or not isinstance(attempts, list) or not 0 <= len(attempts) <= 3:
                raise ValueError("import exceeds bounds")
            for item in files:
                if set(item) != {"path", "bytes", "sha256"} or type(item["bytes"]) is not int or not 0 <= item["bytes"] <= 256 << 10:
                    raise ValueError("invalid import file")
                checked_path(item["path"])
                if not re.fullmatch("[a-f0-9]{64}", item["sha256"]):
                    raise ValueError("invalid file hash")
            if (files != sorted(files, key=lambda f: f["path"]) or len({f["path"] for f in files}) != len(files)
                    or sum(f["bytes"] for f in files) > 16 << 20 or manifest_hash(files) != data["manifestSha256"]):
                raise ValueError("invalid manifest")
            if len(set(attempts)) != len(attempts) or any(not re.fullmatch("attempt-[a-f0-9]{32}", a) for a in attempts):
                raise ValueError("invalid import attempts")
            if data["status"] not in {"PREPARING", "COMMITTED"} or (data["status"] == "COMMITTED" and not attempts):
                raise ValueError("invalid import status")
            old = self.records.get(data["sessionId"])
            if old is None:
                if data["status"] != "PREPARING" or attempts:
                    raise ValueError("import must start before any claim")
                if any(r["workspaceId"] == data["workspaceId"] or r["importId"] == data["importId"]
                       for r in self.records.values()):
                    raise ValueError("import identity already registered")
            else:
                if old["status"] == "COMMITTED":
                    raise ValueError("committed import is immutable")
                if any(old[k] != data[k] for k in old if k not in {"status", "attempts"}):
                    raise ValueError("import source or scope changed")
                if (data["attempts"][:len(old["attempts"])] != old["attempts"]
                        or len(data["attempts"]) > len(old["attempts"]) + 1):
                    raise ValueError("import attempt history changed")
                if data["status"] == "COMMITTED" and attempts != old["attempts"]:
                    raise ValueError("commit cannot allocate an attempt")
        except (ValueError, TypeError, KeyError) as exc:
            raise JournalCorrupt("invalid workspace import history") from exc

    def save(self, data: dict) -> None:
        self._check(data)
        row = self.journal.append("workspace.import", data)
        self.records[data["sessionId"]] = row["data"]

    def get(self, session_id: str) -> dict | None:
        return copy.deepcopy(self.records.get(session_id))

    def close(self):
        self.journal.close()
