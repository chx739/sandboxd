"""离线管理命令：预览/显式导入旧文本工作区，经Go复制到独立NFS目录。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

from .app.clients import SandboxdClient
from .app.redaction import public_error
from .app.runtime.recovery import RecoveryLedger, new_id
from .app.runtime.durable import JournalInUse
from .app.runtime.session import SessionJournal
from .app.runtime.workspace_imports import WorkspaceImports, checked_path, manifest_hash
from .app.tools.remote_files import WorkspaceBinding, RemoteFileWorkspace


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def open_dir(path: Path) -> int:
    """逐级打开已授权的管理源目录，不跟随中间或末尾symlink。"""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.absolute().parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_regular(parent: int, name: str, limit: int) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError("source must be a bounded regular file without hardlinks")
        with os.fdopen(os.dup(fd), "rb") as stream:
            content = stream.read(limit + 1)
        after = os.fstat(fd)
        if len(content) > limit or (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("source changed while reading")
        return content
    finally:
        os.close(fd)


def source_files(root: Path) -> dict[str, bytes]:
    result, count = {}, 0

    def walk(fd, prefix=""):
        nonlocal count
        names = sorted(os.listdir(fd))
        if prefix and not names:
            raise ValueError("empty directories require an explicit separate migration; not silently omitted")
        for name in names:
            count += 1
            if count > 512:
                raise ValueError("source exceeds512 entries")
            path = prefix + name
            checked_path(path)
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    walk(child, path + "/")
                finally:
                    os.close(child)
            else:
                data = read_regular(fd, name, 256 << 10)
                data.decode("utf-8")  # 原文件工具只支持UTF-8文本，不偷偷改变二进制。
                result[path] = data
                if len(result) > 100 or sum(map(len, result.values())) > 16 << 20:
                    raise ValueError("migration exceeds100 files or16 MiB")

    fd = open_dir(root)
    try:
        walk(fd)
    finally:
        os.close(fd)
    return result


def read_file(path: Path, limit=16 << 20) -> bytes:
    fd = open_dir(path.parent)
    try:
        return read_regular(fd, path.name, limit)
    finally:
        os.close(fd)


def inventory(files: dict[str, bytes]) -> list[dict]:
    return [{"path": path, "bytes": len(data), "sha256": digest(data)} for path, data in sorted(files.items())]


async def plan(source: Path, session_path: Path, session_id: str, task_id: str, staged: Path | None = None) -> tuple[dict, dict, bytes]:
    if not re.fullmatch("session-[a-f0-9]{16}", session_id) or not re.fullmatch("task-[a-f0-9]{16}", task_id):
        raise ValueError("invalid legacy session/task identity")
    raw = read_file(session_path)
    files = source_files(source)
    copy = read_file(staged) if staged is not None and staged.exists() else raw
    if not copy.startswith(raw):
        raise ValueError("staged session does not preserve source prefix")
    with tempfile.TemporaryDirectory(prefix="sandboxd-import-view-", dir="/tmp") as directory:
        path = Path(directory) / (session_id + ".jsonl")
        path.write_bytes(copy)
        journal = SessionJournal(Path(directory), session_id)
        summary = await journal.summary()
        if summary["taskId"] != task_id or summary["status"] not in {"succeeded", "failed", "cancelled", "limit_exceeded"}:
            raise ValueError("stop legacy task first and select its matching latest session")
        _, _, node = await journal.load_branch()
        normalized = path.read_bytes()
    rows = inventory(files)
    return {"sessionId": session_id, "sourceTaskId": task_id, "nodeId": node, "sourceSessionSha256": digest(raw),
            "sessionSha256": digest(normalized), "manifestSha256": manifest_hash(rows), "files": rows}, files, normalized


def publish(path: Path, data: bytes):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        if read_file(path) != data:
            raise ValueError("existing control session differs; refusing overwrite")
        return
    parent = open_dir(path.parent)
    temporary = ".import-" + new_id("stage")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        with os.fdopen(os.dup(fd), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # 管理命令持Task锁，私有控制目录没有第二个合法写入者。
        # 先写临时文件再发布，崩溃不能留下看似有效的半份Session。
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            os.rename(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        else:
            raise ValueError("control session appeared during import; refusing overwrite")
        os.fsync(parent)
    finally:
        os.close(fd)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def require_ok(response, scope=None):
    if not 200 <= response.status_code < 300 or not isinstance(response.body, dict):
        raise ValueError("workspace management failed; status=" + str(response.status_code))
    if scope is not None and any(response.body.get(k) != scope[k] for k in ("workspaceId", "sessionId", "branchId")):
        raise ValueError("workspace response scope mismatch")
    return response.body


async def stop_import_owner(client, record):
    current = await client.get_workspace(record["workspaceId"])
    if current.status_code == 404:
        return
    body = require_ok(current, record)
    owner = body.get("owner", {})
    if not owner.get("attemptId"):
        return
    if owner["taskId"] != "task-" + record["importId"] or owner["attemptId"] not in record["attempts"]:
        raise ValueError("import workspace has another owner")
    if body["phase"] == "released" and owner.get("stopConfirmed") is True:
        return
    stopped = require_ok(await client.release_workspace(WorkspaceBinding.from_record(body).as_request()), record)
    if stopped["phase"] != "released" or stopped["owner"].get("stopConfirmed") is not True:
        raise ValueError("import writer stop not confirmed")


async def migrate(*, control: Path, source: Path, session_path: Path, session_id: str, task_id: str,
                  expected_manifest: str, expected_session: str, client) -> dict:
    # 与部署端同一任务锁：agentd运行时拒绝迁移；不绕过活跃任务状态。
    ledger = RecoveryLedger(control / "tasks.jsonl")
    imports = None
    try:
        imports = WorkspaceImports(control / "imports.jsonl")
        if any(s["sessionId"] == session_id for s in ledger.records()):
            raise ValueError("session already has durable tasks; import is only for unmapped legacy sessions")
        staged = control / "import-sessions" / (session_id + ".jsonl")
        info, files, normalized = await plan(source, session_path, session_id, task_id, staged)
        if info["manifestSha256"] != expected_manifest or info["sourceSessionSha256"] != expected_session:
            raise ValueError("source differs from confirmed preview")
        record = imports.get(session_id)
        if record is None:
            publish(staged, normalized)
            record = dict(info, importId=new_id("import"), branchId=new_id("branch"), workspaceId=new_id("workspace"),
                          attempts=[], status="PREPARING")
            imports.save(record)
        elif any(record[k] != info[k] for k in info):
            raise ValueError("pending or committed import source differs")
        if record["status"] == "COMMITTED":
            return record
        await stop_import_owner(client, record)
        if len(record["attempts"]) >= 3:
            raise ValueError("import recovery limit reached; preserve data and inspect manually")
        record["attempts"].append(new_id("attempt"))
        imports.save(record)  # 必须先保存可对账归属，再发出认领请求。
        try:
            require_ok(await client.prepare_workspace(record["workspaceId"], session_id, record["branchId"]), record)
            body = require_ok(await client.claim_workspace(record["workspaceId"], "task-" + record["importId"], record["attempts"][-1]), record)
            if (body.get("phase") != "bound" or body.get("owner", {}).get("taskId") != "task-" + record["importId"]
                    or body["owner"].get("attemptId") != record["attempts"][-1] or body["owner"].get("stopConfirmed") is not False):
                raise ValueError("import owner was not bound")
            remote = RemoteFileWorkspace(client, WorkspaceBinding.from_record(body))
            listed = require_ok(await remote.execute("list_files", {"path": "."}))
            if listed.get("truncated") is not False:
                raise ValueError("cannot verify complete target inventory")
            existing = {f["path"]: f for f in listed["entries"]}
            expected = {f["path"]: f for f in record["files"]}
            if any(path not in expected or row != expected[path] for path, row in existing.items()):
                raise ValueError("target contains unexpected or differing files")
            for path, content in sorted(files.items()):
                if path not in existing:
                    result = require_ok(await remote.execute("write_file", {"path": path, "content": content.decode("utf-8")}))
                    if result.get("newSha256") != expected[path]["sha256"]:
                        raise ValueError("import write hash mismatch")
            final = require_ok(await remote.execute("list_files", {"path": "."}))
            if final.get("truncated") is not False or sorted(final["entries"], key=lambda f: f["path"]) != record["files"]:
                raise ValueError("target manifest verification failed")
            if inventory(source_files(source)) != record["files"] or digest(read_file(session_path)) != expected_session:
                raise ValueError("source changed during import")
        finally:
            # 不移除finalizer、不用NotFound推断停止；结果不明则保持PREPARING。
            await asyncio.wait_for(stop_import_owner(client, record), timeout=65)
        publish(control / "sessions" / (session_id + ".jsonl"), normalized)
        record["status"] = "COMMITTED"
        imports.save(record)
        return record
    finally:
        if imports is not None:
            imports.close()
        ledger.close()


def main():
    parser = argparse.ArgumentParser(description="预览/显式迁移旧文本工作区；先停止agentd，不删除源文件")
    parser.add_argument("--source", type=Path, required=True, help="旧workspace-root/task-id目录")
    parser.add_argument("--session", type=Path, required=True, help="旧Session JSONL")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-manifest")
    parser.add_argument("--expected-session")
    parser.add_argument("--sandboxd-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()

    async def run():
        if not args.apply:
            info, _, _ = await plan(args.source, args.session, args.session_id, args.task_id)
            return dict(info, mode="preview", sourcePreserved=True, externalModelCalls=0)
        if args.control is None or not args.expected_manifest or not args.expected_session:
            raise ValueError("apply requires control and both preview hashes")
        token = os.environ.get("SANDBOXD_TOKEN")
        if not token:
            raise ValueError("SANDBOXD_TOKEN required; Agent token only")
        client = SandboxdClient(args.sandboxd_url, token)
        try:
            return await asyncio.wait_for(migrate(control=args.control, source=args.source, session_path=args.session,
                session_id=args.session_id, task_id=args.task_id, expected_manifest=args.expected_manifest,
                expected_session=args.expected_session, client=client), timeout=600)
        finally:
            await client.close()
    try:
        print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError, JournalInUse, TimeoutError) as exc:
        parser.exit(1, public_error(exc) + "\n")


if __name__ == "__main__":
    main()
