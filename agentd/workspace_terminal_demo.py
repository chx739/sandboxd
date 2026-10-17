"""V1-05真实服务终态验收；四个独立合成Task，全部使用本地Replay。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from .app.clients import SandboxdClient
from .app.redaction import public_error
from .workspace_demo import REPO, checked, ready, start_go, stop_go
from .workspace_process_demo import agent_ready, start_agent, state_for, stop_agent

API = "http://127.0.0.1:18090"
TERMINAL = {"succeeded", "cancelled", "failed", "limit_exceeded", "interrupted"}


async def run(output: Path, binary: Path, nfs_server: str) -> dict:
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nodes = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "get", "nodes", "-o", "json"]))["items"]
    assert len(nodes) == 1 and nodes[0]["metadata"]["name"] == "sandboxd-control-plane"
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert nfs_server in [n["IPAddress"] for n in nfs["NetworkSettings"]["Networks"].values()]
    root = Path.home() / ".local/share/sandboxd/workspace-agent-smoke" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    final = {"content": '{"summary":"terminal fixture completed","recommendation":"local Replay evidence only"}'}
    fixtures = {
        "succeeded": [final],
        # 刻意损坏合成Replay响应参数，让实际Runner进入明确failed，而非伪写状态。
        "failed": [{"toolCalls": [{"id": "malformed-fixture", "name": "read_file", "args": 1}]}],
        "limit_exceeded": [{"toolCalls": [{"id": "list-" + str(i), "name": "list_files", "args": {"path": "."}}]} for i in range(6)],
        "cancelled": [{"delayMs": 60000, **final}],
    }
    paths = {}
    for status, responses in fixtures.items():
        path = root / ("replay-" + status + ".json")
        path.write_text(json.dumps({"kind": "deterministic-policy-case", "model": "terminal-" + status, "responses": responses}))
        path.chmod(0o600)
        paths[status] = path
    # 管理对账入口只需一个可构造Gateway的固定夹具，不运行它的模型。
    (root / "replay.json").write_bytes(paths["succeeded"].read_bytes())
    (root / "replay.json").chmod(0o600)
    go, go_log, go_token = start_go(root, binary, nfs_server)
    api_token, alert_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    sdk = SandboxdClient("http://127.0.0.1:10880", go_token)
    api = httpx.AsyncClient(base_url=API, headers={"Authorization": "Bearer " + api_token}, timeout=5)
    processes, scenarios = [], []
    journal = root / "agent-control/tasks.jsonl"
    report = {"mode": "real-agentd-terminal-restart-replay", "scope": "V1-05 success/cancel/failure/limit survive actual service restart without execution",
              "controlRoot": str(root), "externalModelCalls": 0, "scenarios": scenarios, "passed": False}
    try:
        await ready(go)
        for number, (expected, replay_path) in enumerate(paths.items(), 1):
            process, log = start_agent(root, go_token, api_token, alert_token, number, replay_path)
            processes.append((process, log))
            await agent_ready(process)
            response = await api.post("/api/v1/tasks", json={"summary": "Synthetic terminal " + expected, "labels": {"case": expected}})
            assert response.status_code == 202
            task_id = response.json()["taskId"]
            if expected == "cancelled":
                async with asyncio.timeout(60):
                    while True:
                        state = state_for(journal, task_id)
                        if state and state["attempts"] and state["attempts"][-1]["podUid"] and any(c["kind"] == "model" for c in state["calls"].values()):
                            break
                        await asyncio.sleep(0.1)
                assert (await api.post("/api/v1/tasks/" + task_id + "/cancel")).status_code == 202
            async with asyncio.timeout(75):
                while True:
                    response = await api.get("/api/v1/tasks/" + task_id)
                    assert response.status_code == 200
                    task = response.json()
                    if task["status"] in TERMINAL:
                        break
                    await asyncio.sleep(0.1)
            assert task["status"] == expected, task.get("error")
            state = state_for(journal, task_id)
            assert state["status"] == expected and all(a["ownerStopped"] for a in state["attempts"])
            metadata = await sdk.get_workspace(state["workspaceId"])
            assert metadata.status_code == 200 and metadata.body["phase"] == "released" and metadata.body["owner"]["stopConfirmed"]
            scenarios.append({"expectedStatus": expected, "taskId": task_id, "state": state,
                              "replaySha256": hashlib.sha256(replay_path.read_bytes()).hexdigest(),
                              "workspace": metadata.body, "traces": (await api.get("/api/v1/tasks/" + task_id + "/traces")).json()["attempts"]})
            print(json.dumps({"stage": "terminal-created", "status": expected, "taskId": task_id}), flush=True)
            stop_agent(process, log)
        before = journal.read_bytes()
        session_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "agent-control/sessions").glob("*.jsonl")}
        go_before = (root / "go-control/workspaces.jsonl").read_bytes()
        # 启动第五个真实服务；配置固定success夹具，旧终态不应走到Provider恢复/调用。
        process, log = start_agent(root, go_token, api_token, alert_token, 5, paths["succeeded"])
        processes.append((process, log))
        await agent_ready(process)
        await asyncio.sleep(0.3)
        for scenario in scenarios:
            restored = await api.get("/api/v1/tasks/" + scenario["taskId"])
            assert restored.status_code == 200 and restored.json()["status"] == scenario["expectedStatus"]
            current = state_for(journal, scenario["taskId"])
            assert current == scenario["state"]
            assert (await sdk.get_workspace(current["workspaceId"])).body == scenario["workspace"]
        assert journal.read_bytes() == before
        assert (root / "go-control/workspaces.jsonl").read_bytes() == go_before
        assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "agent-control/sessions").glob("*.jsonl")} == session_hashes
        report.update({"passed": True, "verifiedCases": ["V1-05"], "journalUnchanged": True, "sessionHashes": session_hashes,
                       "goJournalUnchanged": True, "taskJournalSha256": hashlib.sha256(before).hexdigest()})
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": public_error(exc)}
        raise
    finally:
        for process, log in reversed(processes):
            stop_agent(process, log)
        await api.aclose()
        await sdk.close()
        stop_go(go, go_log)
        report["timestamp"] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=REPO / ".cache/workspace-sandboxd")
    parser.add_argument("--nfs-server", required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output, args.binary.resolve(), args.nfs_server))
    print(json.dumps({"passed": result["passed"], "verifiedCases": result.get("verifiedCases", []), "output": str(args.output)}))


if __name__ == "__main__":
    main()
