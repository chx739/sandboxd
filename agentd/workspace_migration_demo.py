"""真实Go/gVisor/NFS旧工作区迁移；合成旧Session、固定Replay，无外部模型。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import HumanMessage, AIMessage

from .app.model_gateway import ReplayModelGateway
from .app.models import AlertEvent
from .app.redaction import public_error
from .app.runner import AgentRunner
from .app.runtime.execution import RecoveryAttempt
from .app.runtime.session import SessionJournal
from .app.runtime.workspace_imports import WorkspaceImports
from .app.store import TaskStore, TaskConflictError
from .workspace_demo import ObservedClient, checked, ready, start_go, stop_go, REPO
from .workspace_migrate import migrate, plan, stop_import_owner, inventory, source_files, digest


class MigrationClient(ObservedClient):
    def __init__(self, token):
        super().__init__(token, lose_write_ack=True)
        self.file_calls = []

    async def workspace_files(self, binding, operation):
        self.file_calls.append({"operation": operation["operation"], "path": operation["path"],
                                "attemptId": binding["attemptId"]})
        return await super().workspace_files(binding, operation)


async def run(output: Path, binary: Path, nfs_server: str):
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert nfs_server in [n["IPAddress"] for n in nfs["NetworkSettings"]["Networks"].values()]
    root = Path.home() / ".local/share/sandboxd/workspace-agent-smoke" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    task_id, session_id = "task-" + uuid4().hex[:16], "session-" + uuid4().hex[:16]
    source = root / "old-workspaces" / task_id
    source.mkdir(mode=0o700, parents=True)
    (source / "draft.md").write_text("synthetic migrated investigation\n")
    (source / "reports").mkdir()
    (source / "reports/结论.md").write_text("合成历史排障记录，不含真实运维数据。\n")
    session = SessionJournal(root / "old-sessions", session_id)
    await session.initialize(task_id, AlertEvent(labels={"cluster": "local-kind"}, annotations={"summary": "synthetic legacy import"}))
    await session.append_transcript(task_id, [HumanMessage(content="old question"), AIMessage(content="old finding"),
                                               HumanMessage(content="next check"), AIMessage(content="saved conclusion")])
    await session.append_result(task_id, "succeeded", "synthetic old task done")
    # 构造明确标记的旧线性格式；不是迁移真实用户数据。
    rows = [json.loads(line) for line in session.path.read_text().splitlines()]
    session.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows if r["type"] not in {"session.node", "session.head"}))
    original = session.path.read_bytes()
    before = inventory(source_files(source))
    control = root / "agent-control"
    preview, _, _ = await plan(source, session.path, session_id, task_id)
    assert not control.exists() and session.path.read_bytes() == original
    process, log, token = start_go(root, binary, nfs_server)
    client = MigrationClient(token)
    worker, store, runner = None, None, None
    report = {"mode": "real-local-services-workspace-migration-replay", "syntheticLegacyData": True,
              "externalModelCalls": 0, "passed": False, "controlRoot": str(root), "preview": preview}
    imported = None

    async def apply():
        return await migrate(control=control, source=source, session_path=session.path, session_id=session_id,
                             task_id=task_id, expected_manifest=preview["manifestSha256"],
                             expected_session=preview["sourceSessionSha256"], client=client)
    try:
        await ready(process)
        try:
            await apply()
        except Exception as exc:
            report["confirmationLoss"] = {"error": public_error(exc), "kind": "client discarded acknowledgment after actual RPC"}
        else:
            raise AssertionError("lost acknowledgment fixture did not interrupt import")
        assert len(client.ack_losses) == 1
        registry = WorkspaceImports(control / "imports.jsonl")
        try:
            pending = registry.get(session_id)
        finally:
            registry.close()
        assert pending["status"] == "PREPARING"
        old = await client.get_workspace(pending["workspaceId"])
        assert old.body["owner"]["stopConfirmed"] is True
        report["pendingAfterLostAck"] = pending
        imported = await apply()
        imports_bytes = (control / "imports.jsonl").read_bytes()
        assert await apply() == imported and (control / "imports.jsonl").read_bytes() == imports_bytes
        assert len(imported["attempts"]) == 2
        writes = [c for c in client.file_calls if c["operation"] == "write_file"]
        assert [c["path"] for c in writes] == ["draft.md", "reports/结论.md"]
        copied = control / "sessions" / (session_id + ".jsonl")
        assert copied.read_bytes().startswith(original) and digest(copied.read_bytes()) == imported["sessionSha256"]
        store = TaskStore(root / "old-traces", recovery_dir=control)
        tree = await store.get_session_tree(session_id)
        old_node = next(n["nodeId"] for n in tree["nodes"] if n["branchable"] and n["nodeId"] != tree["activeLeafId"])
        try:
            await store.resume(session_id, old_node)
        except TaskConflictError:
            report["oldNodeWorkspaceResumeRejected"] = True
        else:
            raise AssertionError("V1 old-node import restored latest files")
        task = await store.resume(session_id)
        assert task.task_id != task_id and task.workspace_id == imported["workspaceId"] and task.branch_id == imported["branchId"]
        script = root / "replay.json"
        script.write_text(json.dumps({"kind": "deterministic-policy-case", "responses": [
            {"toolCalls": [{"id": "read-imported-draft", "name": "read_file", "args": {"path": "draft.md"}},
                           {"id": "read-imported-report", "name": "read_file", "args": {"path": "reports/结论.md"}}]},
            {"content": '{"summary":"Read imported synthetic evidence","recommendation":"observe"}'}]}))
        runner = AgentRunner(object(), client, ReplayModelGateway(script))
        worker = asyncio.create_task(store.worker(runner))
        async with asyncio.timeout(240):
            while True:
                result = await store.get(task.task_id)
                if result.status in {"succeeded", "failed", "interrupted", "limit_exceeded", "cancelled"}:
                    break
                await asyncio.sleep(.1)
        assert result.status == "succeeded", result.error
        traces = await store.get_task_traces(task.task_id)
        assert [s["tool"] for s in traces[0]["steps"]] == ["read_file", "read_file"]
        for step, expected in zip(traces[0]["steps"], before):
            assert json.loads(step["observation"])["body"]["sha256"] == expected["sha256"]
        server = [{"path": f["path"], "sha256": checked(["docker", "exec", "--user", "65532:65532",
            "sandboxd-workspace-nfs-1", "sha256sum", "/export/workspaces/" + imported["workspaceId"] + "/" + f["path"]]).split()[0]} for f in before]
        assert all(a["sha256"] == b["sha256"] for a, b in zip(before, server))
        assert session.path.read_bytes() == original and inventory(source_files(source)) == before
        report.update(passed=True, imported=imported, task=result.model_dump(mode="json", by_alias=True), traces=traces,
                      observations=client.observations, fileCalls=client.file_calls, sourcePreserved=True,
                      copiedSessionPrefixPreserved=True, repeatedApplyUnchanged=True, serverHashes=server,
                      sourceSessionSha256=digest(original), currentSessionSha256=digest(copied.read_bytes()))
    except Exception as exc:
        report["error"] = public_error(exc)
        raise
    finally:
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        try:
            if store is not None:
                for state in store._ledger.records():
                    if state["attempts"] and not state["attempts"][-1]["ownerStopped"]:
                        await runner.stop_attempt(RecoveryAttempt(store._ledger, state["taskId"], state["attempts"][-1]["attemptId"]))
                store.close()
            registry = WorkspaceImports(control / "imports.jsonl")
            try:
                pending = registry.get(session_id)
            finally:
                registry.close()
            if pending is not None:
                # Agent已正常接续时归属已改变，不能让旧import清理删除新owner。
                current = await client.get_workspace(pending["workspaceId"])
                if current.status_code == 200 and current.body.get("owner", {}).get("taskId") == "task-" + pending["importId"]:
                    await stop_import_owner(client, pending)
        except Exception as exc:
            report["cleanupError"] = public_error(exc)
            report["passed"] = False
        await client.close()
        stop_go(process, log)
        report["timestamp"] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    assert report["passed"], report.get("cleanupError")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nfs-server", required=True)
    parser.add_argument("--binary", type=Path, default=REPO / ".cache/workspace-sandboxd")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output, args.binary, args.nfs_server))
    print(json.dumps({"passed": result["passed"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
