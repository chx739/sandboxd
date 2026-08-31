from __future__ import annotations

import difflib
import hashlib
import os
import secrets
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from ..redaction import redact_text

MAX_FILE_BYTES = 256 << 10
MAX_LIST_ENTRIES = 100
MAX_SEARCH_FILES = 100
MAX_SEARCH_BYTES = 1 << 20
MAX_SEARCH_MATCHES = 50
MAX_PATH_BYTES = 512
MAX_PATH_DEPTH = 8
MAX_DIFF_CHARS = 4096


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class FileWorkspace:
    """一次 task 独占的最小文本工作区。

    它不是通用宿主文件系统工具：所有路径都从 task 根目录解析，并在每次访问时
    拒绝符号链接。写入通过 SHA256 CAS + 同目录原子替换避免静默覆盖旧版本。
    """

    def __init__(self, workspace_root: Path, task_id: str) -> None:
        if not task_id.startswith("task-") or len(task_id) > 64:
            raise ValueError("非法 taskId，不能创建文件工作区")
        workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace_root.chmod(0o700)
        self._root = workspace_root / task_id
        self._root.mkdir(mode=0o700, exist_ok=True)
        self._root.chmod(0o700)
        self._root_resolved = self._root.resolve(strict=True)

    @property
    def root(self) -> Path:
        return self._root

    def _parts(self, value: str, *, allow_root: bool = False) -> tuple[str, ...]:
        if not isinstance(value, str) or "\x00" in value or "\\" in value:
            raise ValueError("path 必须是 POSIX 相对路径")
        if value == "." and allow_root:
            return ()
        if not value or value.startswith("/") or value.endswith("/") or "//" in value:
            raise ValueError("path 不能是空值、绝对路径或包含空组件")
        raw_parts = value.split("/")
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise ValueError("path 不能包含空组件、. 或 ..")
        if len(raw_parts) > MAX_PATH_DEPTH or len(value.encode("utf-8")) > MAX_PATH_BYTES:
            raise ValueError("path 超过长度或目录层级上限")
        # PurePosixPath 只用于再次确认语义；真正访问仍使用本地 Path 和 lstat。
        if PurePosixPath(value).is_absolute():
            raise ValueError("path 必须是相对路径")
        return tuple(raw_parts)

    def _path(
        self,
        value: str,
        *,
        allow_root: bool = False,
        allow_missing_leaf: bool = False,
    ) -> Path:
        parts = self._parts(value, allow_root=allow_root)
        current = self._root
        for index, part in enumerate(parts):
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if allow_missing_leaf and index == len(parts) - 1:
                    break
                raise ValueError("path 不存在: %s" % value) from None
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("path 包含符号链接")
        # resolve(strict=False) 负责挡住解析逃逸；lstat 则负责明确拒绝 symlink。
        resolved = current.resolve(strict=False)
        if resolved != self._root_resolved and self._root_resolved not in resolved.parents:
            raise ValueError("path 逃逸 task 工作区")
        return current

    def _ensure_parent(self, path: str) -> Path:
        parts = self._parts(path)
        current = self._root
        for part in parts[:-1]:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o700)
                info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("文件父路径必须是普通目录且不能是符号链接")
            current.chmod(0o700)
        return self._path(path, allow_missing_leaf=True)

    def _read_bytes(self, path: Path) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("只支持普通文本文件")
            if info.st_size > MAX_FILE_BYTES:
                raise ValueError("文件超过 256 KiB 上限")
            value = bytearray()
            while True:
                chunk = os.read(descriptor, min(8192, MAX_FILE_BYTES + 1 - len(value)))
                if not chunk:
                    break
                value.extend(chunk)
                if len(value) > MAX_FILE_BYTES:
                    raise ValueError("文件超过 256 KiB 上限")
            return bytes(value)
        finally:
            os.close(descriptor)

    @staticmethod
    def _decode(value: bytes) -> str:
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("只支持 UTF-8 文本文件") from exc

    def _iter_files(self, base: Path) -> Iterable[Path]:
        if base.is_file():
            yield base
            return
        if not base.is_dir():
            raise ValueError("path 必须是普通文件或目录")
        count = 0
        for directory, dir_names, file_names in os.walk(base, followlinks=False):
            directory_path = Path(directory)
            for name in sorted(dir_names):
                child = directory_path / name
                if stat.S_ISLNK(child.lstat().st_mode):
                    raise ValueError("工作区包含符号链接")
            dir_names.sort()
            for name in sorted(file_names):
                child = directory_path / name
                info = child.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError("工作区包含符号链接")
                if not stat.S_ISREG(info.st_mode):
                    continue
                count += 1
                if count > MAX_SEARCH_FILES:
                    return
                yield child

    def list_files(self, path: str = ".") -> dict[str, Any]:
        base = self._path(path, allow_root=True)
        entries: list[dict[str, Any]] = []
        if base.is_file():
            files = [base]
        else:
            files = list(self._iter_files(base))
        for child in files[:MAX_LIST_ENTRIES]:
            data = self._read_bytes(child)
            entries.append(
                {
                    "path": child.relative_to(self._root).as_posix(),
                    "bytes": len(data),
                    "sha256": _sha256(data),
                }
            )
        return {"path": path, "entries": entries, "truncated": len(files) > len(entries)}

    def read_file(self, path: str, offset: int = 0, limit: int = 16384) -> dict[str, Any]:
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or not 0 <= offset <= MAX_FILE_BYTES
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_FILE_BYTES
        ):
            raise ValueError("offset/limit 超出范围")
        target = self._path(path)
        data = self._read_bytes(target)
        text = self._decode(data)
        visible, redacted = redact_text(text[offset : offset + limit])
        return {
            "path": path,
            "content": visible,
            "sha256": _sha256(data),
            "totalChars": len(text),
            "offset": offset,
            "truncated": offset + limit < len(text),
            "redacted": redacted,
        }

    def search_files(self, query: str, path: str = ".") -> dict[str, Any]:
        if not isinstance(query, str) or not query or len(query.encode("utf-8")) > 256:
            raise ValueError("query 必须为 1 到 256 bytes")
        base = self._path(path, allow_root=True)
        matches: list[dict[str, Any]] = []
        scanned_files = 0
        scanned_bytes = 0
        truncated = False
        for child in self._iter_files(base):
            data = self._read_bytes(child)
            if scanned_bytes + len(data) > MAX_SEARCH_BYTES:
                truncated = True
                break
            scanned_files += 1
            scanned_bytes += len(data)
            text = self._decode(data)
            for line_number, line in enumerate(text.splitlines(), start=1):
                if query not in line:
                    continue
                visible, redacted = redact_text(line[:512])
                matches.append(
                    {
                        "path": child.relative_to(self._root).as_posix(),
                        "line": line_number,
                        "text": visible,
                        "redacted": redacted,
                    }
                )
                if len(matches) >= MAX_SEARCH_MATCHES:
                    truncated = True
                    break
            if truncated:
                break
        return {
            "query": query,
            "matches": matches,
            "scannedFiles": scanned_files,
            "scannedBytes": scanned_bytes,
            "truncated": truncated,
        }

    def _replace(self, target: Path, value: bytes) -> None:
        temporary = target.parent / (".sandboxd-tmp-" + secrets.token_hex(8))
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            written = 0
            while written < len(value):
                written += os.write(descriptor, value[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, target)
            target.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _diff(path: str, old: str, new: str) -> tuple[str, bool]:
        raw = "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=path + ":old",
                tofile=path + ":new",
                n=2,
            )
        )
        visible, redacted = redact_text(raw[:MAX_DIFF_CHARS])
        if len(raw) > MAX_DIFF_CHARS:
            visible += "\n...[diff truncated]"
        return visible, redacted

    def write_file(
        self,
        path: str,
        content: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        target = self._ensure_parent(path)
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise ValueError("文件超过 256 KiB 上限")
        old = b""
        created = not target.exists()
        if created:
            if expected_sha256 is not None:
                raise ValueError("新文件不能携带 expectedSha256")
        else:
            old = self._read_bytes(target)
            if expected_sha256 is None:
                raise ValueError("覆盖现有文件必须提供 expectedSha256")
            if _sha256(old) != expected_sha256:
                raise ValueError("expectedSha256 不匹配，拒绝覆盖")
        self._replace(target, encoded)
        diff, redacted = self._diff(path, self._decode(old), content)
        return {
            "path": path,
            "created": created,
            "oldSha256": _sha256(old) if not created else None,
            "newSha256": _sha256(encoded),
            "bytes": len(encoded),
            "diff": diff,
            "redacted": redacted,
        }

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str,
    ) -> dict[str, Any]:
        target = self._path(path)
        old_bytes = self._read_bytes(target)
        if _sha256(old_bytes) != expected_sha256:
            raise ValueError("expectedSha256 不匹配，拒绝编辑")
        old = self._decode(old_bytes)
        if not old_text or old.count(old_text) != 1:
            raise ValueError("oldText 必须非空且在文件中恰好出现一次")
        new = old.replace(old_text, new_text, 1)
        encoded = new.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise ValueError("编辑后文件超过 256 KiB 上限")
        self._replace(target, encoded)
        diff, redacted = self._diff(path, old, new)
        return {
            "path": path,
            "oldSha256": _sha256(old_bytes),
            "newSha256": _sha256(encoded),
            "bytes": len(encoded),
            "diff": diff,
            "redacted": redacted,
        }
