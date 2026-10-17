"""V1-04：实际 agentd 强杀/重启，核查原调用额度与包含停机时间的截止时间。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import signal
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from .app.clients import SandboxdClient
from .app.model_gateway import ReplayModelGateway
from .app.redaction import public_error
from .app.runner import AgentRunner
from .app.runtime.execution import RecoveryAttempt
from .app.runtime.recovery import RecoveryLedger
from .workspace_demo import REPO, checked, observe_pod, ready, start_go, stop_go
from .workspace_process_demo import API, TERMINAL, agent_ready, entries, start_agent, state_for, stop_agent


def fixture(scenario: str) -> dict:
    if scenario not in {"budget", "expired"}:
        raise ValueError("unknown budget scenario")
    count = 6 if scenario == "budget" else 2
    responses = [{"toolCalls": [{"id": "check-" + str(i), "name": "list_files", "args": {"path": "."}}]}
                 for i in range(count - 1)]
    responses.append({"delayMs": 60000, "content": '{"summary":"unreturned replay response"}'})
    return {"kind": "deterministic-policy-case", "model": "workspace-budget-" + scenario, "responses": responses}


async def run(output: Path, binary: Path, nfs_server: str, scenario: str) -> dict:
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert nfs_server in [n["IPAddress"] for n in nfs["NetworkSettings"]["Networks"].values()]
    root = Path.home() / ".local/share/sandboxd/workspace-agent-smoke" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    replay_path = root / "replay.json"
    replay_path.write_text(json.dumps(fixture(scenario)))
    replay_path.chmod(0o600)
    journal = root / "agent-control/tasks.jsonl"
    go, go_log, go_token = start_go(root, binary, nfs_server)
    sdk = SandboxdClient("http://127.0.0.1:10880", go_token)
    api_token, alert_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    api = httpx.AsyncClient(base_url=API, headers={"Authorization": "Bearer " + api_token}, timeout=5)
    processes = []
    report = {"mode": "real-agentd-budget-restart-replay", "scenario": scenario,
              "controlRoot": str(root), "externalModelCalls": 0, "manualResumeCalls": 0,
              "replaySha256": hashlib.sha256(replay_path.read_bytes()).hexdigest(), "passed": False}
    try:
        await ready(go)
        first, log = start_agent(root, go_token, api_token, alert_token, 1)
        processes.append((first, log))
        await agent_ready(first)
        response = await api.post("/api/v1/tasks", json={"summary": "Synthetic original budget " + scenario,
                                                       "labels": {"cluster": "local-kind", "case": scenario}})
        assert response.status_code == 202
        task_id = response.json()["taskId"]
        expected_slots = 6 if scenario == "budget" else 2
        async with asyncio.timeout(65):
            while True:
                before = state_for(journal, task_id)
                if before and sum(c["kind"] == "model" for c in before["calls"].values()) == expected_slots:
                    if any(c["kind"] == "model" and c["state"] == "reserved" for c in before["calls"].values()):
                        break
                assert (await api.get("/api/v1/tasks/" + task_id)).json()["status"] not in TERMINAL
                await asyncio.sleep(0.1)
        assert before["completeNodeId"] and before["timing"]["loopSeconds"] == 120
        metadata = await sdk.get_workspace(before["workspaceId"])
        assert metadata.status_code == 200 and metadata.body["phase"] == "bound"
        report["podBeforeKill"] = observe_pod(metadata.body)
        before = state_for(journal, task_id)
        session = root / "agent-control/sessions" / (before["sessionId"] + ".jsonl")
        prefix = session.read_bytes()
        first.kill()
        assert first.wait(timeout=8) == -signal.SIGKILL
        report.update({"stateBeforeKill": before, "kill": {"signal": "SIGKILL", "returnCode": first.returncode,
                                                          "timestamp": datetime.now(timezone.utc).isoformat()}})
        print(json.dumps({"stage": "budget-sigkill", "scenario": scenario, "taskId": task_id, "modelSlots": expected_slots}), flush=True)
        if scenario == "expired":
            # 使用真实墙钟等待原截止时间，禁止修改账本或给测试任务重发时钟。
            deadline = datetime.fromisoformat(before["deadline"])
            while True:
                remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
                if remaining <= -0.2:
                    break
                print(json.dumps({"stage": "waiting-original-deadline", "remainingSeconds": round(remaining, 2)}), flush=True)
                await asyncio.sleep(min(20, max(0.2, remaining + 0.2)))
        report["restartRequestedAt"] = datetime.now(timezone.utc).isoformat()
        second, log = start_agent(root, go_token, api_token, alert_token, 2)
        processes.append((second, log))
        await agent_ready(second)
        async with asyncio.timeout(45):
            while True:
                response = await api.get("/api/v1/tasks/" + task_id)
                assert response.status_code == 200
                task = response.json()
                if task["status"] in TERMINAL:
                    break
                await asyncio.sleep(0.1)
        assert task["status"] == "limit_exceeded", task.get("error")
        after = state_for(journal, task_id)
        for key in ("taskId", "sessionId", "branchId", "workspaceId", "deadline", "timing", "limits", "provider"):
            assert after[key] == before[key], key
        assert set(after["calls"]) == set(before["calls"]), "restart issued another external call"
        assert all(a["ownerStopped"] for a in after["attempts"])
        uncertain = [key for key, call in before["calls"].items() if call["state"] == "reserved"]
        assert uncertain and all(after["calls"][key]["state"] == "uncertain" for key in uncertain)
        assert session.read_bytes().startswith(prefix)
        final_go = await sdk.get_workspace(before["workspaceId"])
        assert final_go.status_code == 200 and final_go.body["phase"] == "released"
        assert final_go.body["generation"] == metadata.body["generation"]
        assert final_go.body["owner"]["stopConfirmed"]
        if scenario == "budget":
            assert expected_slots == before["limits"]["model"]
            assert len(after["attempts"]) == 2 and not after["attempts"][-1]["claimRequested"]
            assert datetime.now(timezone.utc) < datetime.fromisoformat(after["deadline"]), "fixture hit deadline rather than model budget"
            assert "model call budget" in task["error"]
        else:
            assert len(after["attempts"]) == 1
            assert "deadline" in task["error"]
            assert datetime.fromisoformat(report["restartRequestedAt"]) > datetime.fromisoformat(before["deadline"])
        report.update({"stateAfterRestart": after, "taskAfterRestart": task, "goAfterRestart": final_go.body,
                       "traces": (await api.get("/api/v1/tasks/" + task_id + "/traces")).json()["attempts"],
                       "sessionPrefixPreserved": True, "additionalCalls": 0, "newPodClaims": 0,
                       "passed": True, "verifiedScope": "V1-04 " + scenario})
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": public_error(exc)}
        raise
    finally:
        for process, log in reversed(processes):
            stop_agent(process, log)
        ledger = None
        try:
            if journal.exists():
                ledger = RecoveryLedger(journal)
                runner = AgentRunner(object(), sdk, ReplayModelGateway(replay_path))
                for state in ledger.records():
                    if state["attempts"] and not state["attempts"][-1]["ownerStopped"]:
                        await runner.stop_attempt(RecoveryAttempt(ledger, state["taskId"], state["attempts"][-1]["attemptId"]))
                report["goJournalRecords"] = len(entries(root / "go-control/workspaces.jsonl", go_format=True))
        except Exception as exc:
            report["cleanupError"] = public_error(exc)
            report["passed"] = False
        finally:
            if ledger is not None:
                ledger.close()
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
    parser.add_argument("--scenario", choices=("budget", "expired"), required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output, args.binary.resolve(), args.nfs_server, args.scenario))
    print(json.dumps({"passed": result["passed"], "scenario": args.scenario, "output": str(args.output)}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
