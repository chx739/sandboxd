"""V1-14：真实接收steer/follow-up后SIGKILL，恢复命令游标与旧attempt审计。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import signal
import time
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

CONTENT = "synthetic command recovery guard\n"


def fixture() -> dict:
    return {"kind": "deterministic-policy-case", "model": "workspace-command-recovery", "responses": [
        {"toolCalls": [{"id": "write-before-commands", "name": "write_file", "args": {"path": "guard.md", "content": CONTENT}}]},
        {"delayMs": 60000, "toolCalls": [{"id": "unreturned-read", "name": "read_file", "args": {"path": "guard.md"}}]},
        {"toolCalls": [{"id": "read-after-command-recovery", "name": "read_file", "args": {"path": "guard.md"}}]},
        {"delayMs": 8000, "content": '{"summary":"Recovered guard read; finish initial task"}'},
        {"content": '{"summary":"Follow-up explains saved evidence; Replay only"}'},
    ]}


async def run(output: Path, binary: Path, nfs_server: str) -> dict:
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    server = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert nfs_server in [n["IPAddress"] for n in server["NetworkSettings"]["Networks"].values()]
    root = Path.home() / ".local/share/sandboxd/workspace-agent-smoke" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    replay = root / "replay.json"
    replay.write_text(json.dumps(fixture()))
    replay.chmod(0o600)
    journal = root / "agent-control/tasks.jsonl"
    go, go_log, go_token = start_go(root, binary, nfs_server)
    sdk = SandboxdClient("http://127.0.0.1:10880", go_token)
    api_token, alert_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    api = httpx.AsyncClient(base_url=API, headers={"Authorization": "Bearer " + api_token}, timeout=5)
    processes, requests = [], []
    report = {"mode": "real-agentd-command-sigkill-replay", "scope": "V1-14 accepted pending commands, old audit Trace and terminal restart",
              "controlRoot": str(root), "externalModelCalls": 0, "apiRequests": requests,
              "replaySha256": hashlib.sha256(replay.read_bytes()).hexdigest(), "passed": False}

    async def request(method, path, **kwargs):
        response = await api.request(method, path, **kwargs)
        requests.append({"method": method, "path": path, "statusCode": response.status_code})
        return response

    try:
        await ready(go)
        first, log = start_agent(root, go_token, api_token, alert_token, 1)
        processes.append((first, log))
        await agent_ready(first)
        response = await request("POST", "/api/v1/tasks", json={"summary": "Synthetic acknowledged command recovery",
                                                              "labels": {"cluster": "local-kind", "case": "commands"}})
        assert response.status_code == 202
        task_id = response.json()["taskId"]
        async with asyncio.timeout(65):
            while True:
                before = state_for(journal, task_id)
                if before and sum(c["kind"] == "model" for c in before["calls"].values()) == 2:
                    if any(c["kind"] == "model" and c["state"] == "reserved" for c in before["calls"].values()):
                        break
                assert (await request("GET", "/api/v1/tasks/" + task_id)).json()["status"] not in TERMINAL
                await asyncio.sleep(.1)
        old_go = await sdk.get_workspace(before["workspaceId"])
        assert old_go.status_code == 200 and old_go.body["phase"] == "bound"
        old_pod = observe_pod(old_go.body)
        for kind, content in (("steer", "Only inspect the saved guard; keep scope unchanged."),
                              ("follow-up", "Explain which saved evidence supports the answer.")):
            accepted = await request("POST", "/api/v1/tasks/" + task_id + "/" + kind, json={"content": content})
            assert accepted.status_code == 202 and accepted.json()["accepted"] == kind
        before = state_for(journal, task_id)
        assert len(before["commands"]) == 2 and all(c["appliedNodeId"] is None for c in before["commands"])
        command_ids = [c["commandId"] for c in before["commands"]]
        audit_before = [e["data"] for e in entries(journal) if e["type"] == "call.audit"]
        assert len(audit_before) == 2  # 第一模型响应与写工具结果；在途模型不伪造响应。
        session_path = root / "agent-control/sessions" / (before["sessionId"] + ".jsonl")
        prefix = session_path.read_bytes()
        first.kill()
        assert first.wait(timeout=8) == -signal.SIGKILL
        killed = time.perf_counter()
        report.update({"stateBeforeKill": before, "podBeforeKill": old_pod, "auditsBeforeKill": audit_before,
                       "kill": {"pid": first.pid, "signal": "SIGKILL", "returnCode": first.returncode}})
        print(json.dumps({"stage": "commands-accepted-and-sigkill", "taskId": task_id, "commandIds": command_ids}), flush=True)
        second, log = start_agent(root, go_token, api_token, alert_token, 2)
        processes.append((second, log))
        await agent_ready(second)
        async with asyncio.timeout(45):
            while True:
                state = state_for(journal, task_id)
                new_go = await sdk.get_workspace(before["workspaceId"])
                if len(state["attempts"]) == 2 and new_go.status_code == 200 and new_go.body["phase"] == "bound":
                    if new_go.body["owner"]["attemptId"] == state["attempts"][-1]["attemptId"]:
                        break
                assert (await request("GET", "/api/v1/tasks/" + task_id)).json()["status"] not in TERMINAL
                await asyncio.sleep(.1)
        new_pod = observe_pod(new_go.body)
        assert new_pod["podUid"] != old_pod["podUid"] and new_pod["imageId"] == old_pod["imageId"]
        async with asyncio.timeout(35):
            while True:
                task = (await request("GET", "/api/v1/tasks/" + task_id)).json()
                if task["status"] in TERMINAL:
                    break
                await asyncio.sleep(.1)
        assert task["status"] == "succeeded", task.get("error")
        after = state_for(journal, task_id)
        for key in ("taskId", "sessionId", "branchId", "workspaceId", "limits", "deadline", "timing", "provider"):
            assert before[key] == after[key]
        assert len(after["attempts"]) == 2 and all(a["ownerStopped"] for a in after["attempts"])
        assert sum(c["kind"] == "model" for c in after["calls"].values()) == 5
        assert sum(c["operation"] == "write_file" for c in after["calls"].values()) == 1
        assert [c["commandId"] for c in after["commands"]] == command_ids
        assert all(c["appliedNodeId"] for c in after["commands"])
        assert session_path.read_bytes().startswith(prefix)
        tree = (await request("GET", "/api/v1/sessions/" + after["sessionId"] + "/tree")).json()
        path = (await request("GET", "/api/v1/sessions/" + after["sessionId"] + "/path/" + tree["activeLeafId"])).json()
        nodes = [json.loads(line) for line in session_path.read_text().splitlines() if json.loads(line)["type"] == "session.node"]
        for cid in command_ids:
            assert sum(n["message"].get("commandId") == cid for n in nodes) == 1
            assert sum(m.get("commandId") == cid for m in path["messages"]) == 1
        traces = (await request("GET", "/api/v1/tasks/" + task_id + "/traces")).json()["attempts"]
        assert len(traces) == 2 and traces[0]["mode"] == "recovery"
        details = traces[0]["events"][0]["details"]
        assert details["usageKnown"] is False and details["modelReservations"] == 2 and details["modelResponsesRecorded"] == 1
        assert details["auditIds"] == [a["auditId"] for a in audit_before]
        assert [s["tool"] for s in traces[0]["steps"]] == ["write_file"]
        assert traces[0]["steps"][0]["arguments"]["content"]["redacted"] is True
        old_evidence = json.loads(traces[0]["steps"][0]["observation"])["evidenceId"]
        assert old_evidence in prefix.decode()
        audits_after = [e["data"] for e in entries(journal) if e["type"] == "call.audit"]
        assert audits_after[:len(audit_before)] == audit_before
        expected = hashlib.sha256(CONTENT.encode()).hexdigest()
        assert checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum",
                        "/export/workspaces/" + after["workspaceId"] + "/guard.md"]).split()[0] == expected
        report.update({"stateAfterRestart": after, "podAfterRestart": new_pod, "traces": traces,
                       "auditsAfterRestart": audits_after, "commandIds": command_ids, "commandNodeCounts": [1, 1],
                       "oldToolEvidenceId": old_evidence, "serverSha256": expected,
                       "killToTaskSucceededMs": round((time.perf_counter() - killed) * 1000, 3), "sessionPrefixPreserved": True})
        stable = journal.read_bytes()
        session_stable = session_path.read_bytes()
        go_stable = (root / "go-control/workspaces.jsonl").read_bytes()
        stop_agent(second, processes[-1][1])
        third, log = start_agent(root, go_token, api_token, alert_token, 3)
        processes.append((third, log))
        await agent_ready(third)
        await asyncio.sleep(.3)
        assert (await request("GET", "/api/v1/tasks/" + task_id)).json()["status"] == "succeeded"
        assert journal.read_bytes() == stable and session_path.read_bytes() == session_stable
        assert (root / "go-control/workspaces.jsonl").read_bytes() == go_stable
        assert (await request("GET", "/api/v1/tasks/" + task_id + "/traces")).json()["attempts"] == traces
        report.update({"terminalRestartUnchanged": True, "verifiedCases": ["V1-14"], "passed": True})
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
                runner = AgentRunner(object(), sdk, ReplayModelGateway(replay))
                for state in ledger.records():
                    if state["attempts"] and not state["attempts"][-1]["ownerStopped"]:
                        await runner.stop_attempt(RecoveryAttempt(ledger, state["taskId"], state["attempts"][-1]["attemptId"]))
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
    args = parser.parse_args()
    result = asyncio.run(run(args.output, args.binary.resolve(), args.nfs_server))
    print(json.dumps({"passed": result["passed"], "output": str(args.output)}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
