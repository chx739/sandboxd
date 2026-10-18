"""真实Agent ASGI处理器/TaskStore + Go HTTP/gVisor/NFS/restic/MinIO历史分支；模型为Replay。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import socket
import time
from unittest.mock import patch
from uuid import uuid4

import httpx

from .app.config import Settings
from .app.main import create_app
from .app.redaction import public_error
from .app.store import TaskStore
from .app.runtime.recovery import new_id
from .workspace_checkpoint_demo import CheckpointObservedClient
from .workspace_checkpoint_fault_demo import observe_maintenance
from .workspace_demo import REPO, checked, ready, start_go, stop_go

MARKER = "synthetic-branch-A-only-marker"


class RestoreObservedClient(CheckpointObservedClient):
    def __init__(self, token):
        super().__init__(token)
        self.restores = []

    async def workspace_restore(self, action, target, cp, job, source, *, timeout=90):
        response = await super().workspace_restore(action, target, cp, job, source, timeout=timeout)
        if action == "wait" and response.status_code == 200:
            runtime = observe_maintenance(response.body["barrier"])
            assert runtime and runtime["actualRuntimeType"] == "io.containerd.runsc.v1"
            self.restores.append({"restoreId": job, "checkpointId": cp, "target": target,
                                  "result": response.body["result"], "actualRuntime": runtime})
        return response


async def run(output: Path, source_report: Path, secret: str, endpoint: str):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1);probe.bind(("127.0.0.1", 10880))
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert nfs["State"]["Running"] and not nfs["State"]["Paused"]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    server = nfs["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    source = json.loads(source_report.read_text());assert source["passed"]
    cp = source["checkpointAtRestart"];assert cp["status"] == "COMMITTED"
    sid, source_task = cp["identity"]["sessionId"], cp["identity"]["taskId"]
    original = Path(source["controlRoot"]) / "agent-control"
    expected_parent = Path.home() / ".local/share/sandboxd/workspace-checkpoint-fault"
    assert original.parent.resolve().parent == expected_parent.resolve()
    assert len(original.parent.name) == 32 and all(c in "0123456789abcdef" for c in original.parent.name)
    original_hashes = {str(p.relative_to(original)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in original.rglob("*") if p.is_file()}
    root = Path.home() / ".local/share/sandboxd/workspace-restore-branch" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    shutil.copytree(original, root / "agent-control")
    binary = REPO / ".cache/workspace-sandboxd-restore-http"
    go, log, token = start_go(root, binary, server, backup_secret=secret, backup_minio_ip=endpoint,
                              backup_image="sandboxd-restic:0.18.1-v2")
    report = {"passed": False, "mode": "actual-agent-asgi-go-http-gvisor-nfs-restic-minio-history-branches",
              "externalModelCalls": 0, "controlRoot": str(root), "sourceCheckpointId": cp["checkpointId"],
              "sourceReport": str(source_report), "cases": [], "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "scope": "two historical branch restores via actual ASGI handlers and local services; no Agent TCP, SIGKILL, GC or Live LLM"}
    started = time.perf_counter()
    def server_file_hash(workspace, name):
        return checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum",
                        "/export/workspaces/" + workspace + "/" + name]).split()[0]
    source_hash = server_file_hash(cp["identity"]["workspaceId"], "report.md")
    try:
        await ready(go)
        for index in (1, 2):
            script = root / ("branch-" + str(index) + ".json")
            responses = [{"toolCalls": [{"id": "branch-read", "name": "read_file", "args": {"path": "report.md"}}]}]
            if index == 1:
                responses.append({"toolCalls": [{"id": "branch-new-write", "name": "write_file",
                                                 "args": {"path": "branch-only.md", "content": MARKER}}]})
            responses.append({"content": '{"summary":"Historical branch replay verified","recommendation":"review evidence"}'})
            script.write_text(json.dumps({"kind": "deterministic-policy-case", "model": "historical-branch-replay", "responses": responses}))
            script.chmod(0o600)
            api_token = secrets.token_urlsafe(32)
            cfg = Settings(listen_host="127.0.0.1", listen_port=18090, api_token=api_token, alert_token=secrets.token_urlsafe(32),
                           prometheus_url="http://127.0.0.1:19090", sandboxd_url="http://127.0.0.1:10880", sandboxd_token=token,
                           llm_mode="replay", llm_base_url="", llm_model="", llm_api_key="", llm_thinking="default",
                           replay_file=script, trace_dir=root / "legacy-traces", workspace_backend="nfs", recovery_dir=root / "agent-control",
                           checkpoint_mode="restic", checkpoint_repository_id=cp["data"]["repositoryId"])
            captured = {}
            def sdk_factory(url, auth):
                assert url == "http://127.0.0.1:10880"
                captured["sdk"] = RestoreObservedClient(auth);return captured["sdk"]
            def store_factory(*args, **kwargs):
                captured["store"] = TaskStore(*args, **kwargs);return captured["store"]
            # 仅管理夹具观察实际RPC与CRI；原处理器、协调器、TaskStore/Runner均未替换。
            with patch("agentd.app.main.SandboxdClient", sdk_factory), patch("agentd.app.main.TaskStore", store_factory):
                app = create_app(cfg)
            async with app.router.lifespan_context(app):
                store, sdk = captured["store"], captured["sdk"]
                before_source = store._ledger.get(source_task)
                source_prefix = store._restorer.ledger.restore_point(cp["checkpointId"])[1]
                source_head = store._restorer.ledger.head(sid, cp["identity"]["branchId"])
                rid = new_id("restore")
                path = f"/api/v1/sessions/{sid}/checkpoints/{cp['checkpointId']}/restore/{rid}"
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test",
                                            headers={"Authorization": "Bearer " + api_token}) as api:
                    denied = await api.post(path, headers={"Authorization": "Bearer unauthorized"});assert denied.status_code == 401
                    created = await api.post(path);assert created.status_code == 202, created.text
                    task_id = created.json()["taskId"]
                    retry = await api.post(path);assert retry.status_code == 202 and retry.json()["taskId"] == task_id
                    async with asyncio.timeout(240):
                        while True:
                            status = await api.get("/api/v1/tasks/" + task_id);assert status.status_code == 200
                            task = status.json()
                            if task["status"] in {"succeeded", "failed", "cancelled", "limit_exceeded", "interrupted"}:break
                            await asyncio.sleep(.1)
                    assert task["status"] == "succeeded", task.get("error")
                    progress = await api.get(f"/api/v1/sessions/{sid}/restores/{rid}")
                    assert progress.status_code == 200 and progress.json()["status"] == "ATTACHED"
                state = store._ledger.get(task_id)
                head = store._restorer.ledger.head(sid, state["branchId"])
                chain = [];current = head
                while current["checkpointId"] != cp["checkpointId"]:
                    assert current["identity"]["taskId"] == task_id
                    chain.append(current)
                    current = store._restorer.ledger.get(current["parentCheckpointId"])
                assert chain[-1]["revision"] == 1 and head["revision"] == len(responses)
                prefix = store._restorer.ledger.restore_point(head["checkpointId"])[1]
                assert prefix["nodes"][:len(source_prefix["nodes"])] == source_prefix["nodes"]
                assert store._ledger.get(source_task) == before_source
                assert store._restorer.ledger.head(sid, cp["identity"]["branchId"]) == source_head
                assert sum(c["kind"] == "model" for c in state["calls"].values()) == len(responses)
                assert sum(c["kind"] == "tool" and c["operation"] == "write_file" for c in state["calls"].values()) == (1 if index == 1 else 0)
                assert server_file_hash(state["workspaceId"], "report.md") == source_hash == source["fileSha256"]
                names = checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "ls", "-A",
                                 "/export/workspaces/" + state["workspaceId"]]).splitlines()
                assert ("branch-only.md" in names) == (index == 1)
                if index == 2:assert MARKER not in json.dumps(prefix)
                assert len(sdk.restores) == 1 and sdk.restores[0]["result"]["manifestSha256"] == cp["data"]["manifestSha256"]
                assert all(p["imageId"] == cp["identity"]["imageId"] for p in sdk.observations)
                report["cases"].append({"index": index, "passed": True, "taskId": task_id, "branchId": state["branchId"],
                    "workspaceId": state["workspaceId"], "restore": progress.json(), "sourceTaskUnchanged": True,
                    "sourceBranchHeadUnchanged": True, "sourcePrefixPreserved": True, "siblingMarkerAbsent": index == 2,
                    "fileSha256": source_hash, "branchFilePresent": index == 1, "checkpoints": chain,
                    "maintenance": sdk.maintenance, "restoreMaintenance": sdk.restores, "businessPods": sdk.observations,
                    "modelReservations": len(responses), "toolReservations": len(responses) - 1, "attempts": state["attempts"]})
        assert server_file_hash(cp["identity"]["workspaceId"], "report.md") == source_hash
        assert all(hashlib.sha256((original / name).read_bytes()).hexdigest() == digest for name, digest in original_hashes.items())
        report.update(passed=True, originalControlUnchanged=True, elapsedMs=(time.perf_counter()-started)*1000)
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "message": public_error(error)}
        raise
    finally:
        stop_go(go, log)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--secret-name", required=True);parser.add_argument("--minio-ip", required=True)
    args = parser.parse_args()
    if args.output.exists():raise ValueError("preserve existing evidence; choose a new report")
    result = asyncio.run(run(args.output, args.source_report, args.secret_name, args.minio_ip))
    print(json.dumps({"passed": result["passed"], "cases": len(result["cases"]), "scope": result["scope"]}))
