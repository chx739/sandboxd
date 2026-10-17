"""V1-06：真实重复服务拒绝、过期owner清理拒绝与取消/Pod接管竞态。"""
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
from .app.tools.remote_files import WorkspaceBinding
from .workspace_demo import REPO, checked, observe_pod, ready, start_go, stop_go
from .workspace_process_demo import API, TERMINAL, agent_ready, entries, start_agent, state_for, stop_agent

CONTENT = "synthetic concurrency guard\n"


def fixture() -> dict:
    return {"kind": "deterministic-policy-case", "model": "workspace-concurrency", "responses": [
        {"toolCalls": [{"id": "write-guard", "name": "write_file", "args": {"path": "guard.md", "content": CONTENT}}]},
        {"delayMs": 15000, "toolCalls": [{"id": "read-after-fault", "name": "read_file", "args": {"path": "guard.md"}}]},
        {"delayMs": 60000, "content": '{"summary":"unreturned response for cancellation fixture"}'},
    ]}


async def run(output: Path, binary: Path, nfs_server: str) -> dict:
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert nfs_server in [n["IPAddress"] for n in nfs["NetworkSettings"]["Networks"].values()]
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
    report = {"mode": "real-agentd-concurrency-replay", "scope": "V1-06, bounded local single-node races",
              "controlRoot": str(root), "externalModelCalls": 0, "apiRequests": requests, "passed": False}

    async def request(method, path, **kwargs):
        response = await api.request(method, path, **kwargs)
        requests.append({"method": method, "path": path, "statusCode": response.status_code})
        return response

    async def waiting_model(task_id):
        async with asyncio.timeout(65):
            while True:
                state = state_for(journal, task_id)
                if state and sum(c["kind"] == "model" for c in state["calls"].values()) == 2:
                    if any(c["kind"] == "model" and c["state"] == "reserved" for c in state["calls"].values()):
                        assert state["completeNodeId"]
                        return state
                response = await request("GET", "/api/v1/tasks/" + task_id)
                assert response.status_code == 200 and response.json()["status"] not in TERMINAL
                await asyncio.sleep(.05)

    async def cancelled(task_id):
        async with asyncio.timeout(35):
            while True:
                response = await request("GET", "/api/v1/tasks/" + task_id)
                assert response.status_code == 200
                task = response.json()
                if task["status"] in TERMINAL:
                    assert task["status"] == "cancelled", task.get("error")
                    state = state_for(journal, task_id)
                    assert state["cancelRequested"] and all(a["ownerStopped"] for a in state["attempts"])
                    return state
                await asyncio.sleep(.05)

    try:
        await ready(go)
        first, log = start_agent(root, go_token, api_token, alert_token, 1)
        processes.append((first, log))
        await agent_ready(first)
        stable = journal.read_bytes()
        # 使用不同HTTP端口，排除“端口冲突”被误当成控制目录单写入者保护。
        duplicate, log = start_agent(root, go_token, api_token, alert_token, 2, port=18091)
        processes.append((duplicate, log))
        async with asyncio.timeout(100):
            while duplicate.poll() is None:
                await asyncio.sleep(.1)
        text = (root / "agentd-process-2.log").read_text()
        assert duplicate.returncode != 0 and "JournalInUse" in text and "active owner" in text
        assert "address already in use" not in text.lower() and journal.read_bytes() == stable
        assert first.poll() is None and (await api.get("/readyz")).status_code == 200
        report["duplicateProcess"] = {"pid": duplicate.pid, "port": 18091, "returnCode": duplicate.returncode,
                                      "rejectedByJournalLock": True, "journalUnchanged": True,
                                      "logSha256": hashlib.sha256(text.encode()).hexdigest()}
        print(json.dumps({"stage": "duplicate-service-rejected-by-lock"}), flush=True)

        created = await request("POST", "/api/v1/tasks", json={"summary": "Synthetic stale cleanup after restart",
                                                               "labels": {"cluster": "local-kind", "case": "stale-release"}})
        assert created.status_code == 202
        task_id = created.json()["taskId"]
        before = await waiting_model(task_id)
        old_go = await sdk.get_workspace(before["workspaceId"])
        assert old_go.status_code == 200 and old_go.body["phase"] == "bound"
        old_pod = observe_pod(old_go.body)
        old_binding = WorkspaceBinding.from_record(old_go.body).as_request()
        first.kill()
        assert first.wait(timeout=8) == -signal.SIGKILL
        resumed, log = start_agent(root, go_token, api_token, alert_token, 3)
        processes.append((resumed, log))
        await agent_ready(resumed)
        async with asyncio.timeout(45):
            while True:
                current = state_for(journal, task_id)
                new_go = await sdk.get_workspace(before["workspaceId"])
                if len(current["attempts"]) == 2 and new_go.status_code == 200 and new_go.body["phase"] == "bound":
                    if new_go.body["owner"]["attemptId"] == current["attempts"][-1]["attemptId"]:
                        break
                assert (await request("GET", "/api/v1/tasks/" + task_id)).json()["status"] not in TERMINAL
                await asyncio.sleep(.05)
        new_pod = observe_pod(new_go.body)
        assert new_pod["podUid"] != old_pod["podUid"]
        stale = await sdk.release_workspace(old_binding)
        assert stale.status_code == 409
        unchanged = await sdk.get_workspace(before["workspaceId"])
        assert unchanged.body == new_go.body
        read = await sdk.workspace_files(WorkspaceBinding.from_record(new_go.body).as_request(),
                                         {"operation": "read_file", "path": "guard.md"})
        assert read.status_code == 200 and read.body["sha256"] == hashlib.sha256(CONTENT.encode()).hexdigest()
        duplicates = await asyncio.gather(*(request("POST", "/api/v1/sessions/" + before["sessionId"] + "/resume") for _ in range(2)))
        assert [r.status_code for r in duplicates] == [409, 409]
        assert len({r["data"]["taskId"] for r in entries(journal) if r["type"] == "task.state"}) == 1
        cancellations = await asyncio.gather(*(request("POST", "/api/v1/tasks/" + task_id + "/cancel") for _ in range(2)))
        assert all(r.status_code in {202, 409} for r in cancellations) and any(r.status_code == 202 for r in cancellations)
        after = await cancelled(task_id)
        assert len(after["attempts"]) == 2
        for key in ("taskId", "sessionId", "branchId", "workspaceId", "limits", "deadline", "timing"):
            assert before[key] == after[key]
        report["staleCleanup"] = {"before": before, "after": after, "oldPod": old_pod, "newPod": new_pod,
                                  "staleReleaseStatus": stale.status_code, "newOwnerUnchangedAfterStaleRelease": True,
                                  "newOwnerReadSha256": read.body["sha256"], "duplicateResumeStatuses": [r.status_code for r in duplicates],
                                  "duplicateCancelStatuses": [r.status_code for r in cancellations]}
        print(json.dumps({"stage": "stale-cleanup-and-duplicate-requests-verified", "taskId": task_id}), flush=True)

        created = await request("POST", "/api/v1/tasks", json={"summary": "Synthetic cancellation during Pod takeover",
                                                               "labels": {"cluster": "local-kind", "case": "cancel-during-claim"}})
        assert created.status_code == 202
        task_id = created.json()["taskId"]
        before = await waiting_model(task_id)
        old_go = await sdk.get_workspace(before["workspaceId"])
        old_pod = observe_pod(old_go.body)
        checked(["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-demo", "delete", "pod",
                 old_go.body["owner"]["podName"], "--wait=false", "--grace-period=1"])
        async with asyncio.timeout(45):
            while True:
                state = state_for(journal, task_id)
                if len(state["attempts"]) == 2 and state["attempts"][-1].get("claimRequested"):
                    taking_over = await sdk.get_workspace(before["workspaceId"])
                    if taking_over.status_code == 200 and taking_over.body["phase"] == "starting":
                        assert taking_over.body["owner"]["attemptId"] == state["attempts"][-1]["attemptId"]
                        break
                    if taking_over.status_code == 200 and taking_over.body["phase"] == "bound":
                        raise AssertionError("fixture missed starting window; not a cancel-during-claim pass")
                assert (await request("GET", "/api/v1/tasks/" + task_id)).json()["status"] not in TERMINAL
                await asyncio.sleep(.05)
        observed_at = datetime.now(timezone.utc).isoformat()
        accepted = await request("POST", "/api/v1/tasks/" + task_id + "/cancel")
        assert accepted.status_code == 202
        after = await cancelled(task_id)
        assert len(after["attempts"]) == 2 and sum(c["kind"] == "model" for c in after["calls"].values()) == 2
        for key in ("taskId", "sessionId", "branchId", "workspaceId", "limits", "deadline", "timing"):
            assert before[key] == after[key]
        cancelled_event = next(e for e in entries(journal) if e["type"] == "task.state" and e["data"]["taskId"] == task_id and e["data"]["cancelRequested"])
        go_events = [e for e in entries(root / "go-control/workspaces.jsonl", go_format=True) if e["data"]["workspaceId"] == before["workspaceId"]]
        attempt_id = after["attempts"][-1]["attemptId"]
        bound_events = [e for e in go_events if e["data"]["phase"] == "bound" and e["data"]["owner"]["attemptId"] == attempt_id]
        assert all(datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")) >= datetime.fromisoformat(cancelled_event["timestamp"]) for e in bound_events)
        released = await sdk.get_workspace(before["workspaceId"])
        assert released.body["phase"] == "released" and released.body["owner"]["stopConfirmed"]
        report["cancelDuringTakeover"] = {"before": before, "after": after, "oldPod": old_pod,
                                          "observedAt": observed_at, "goAtCancelRequest": taking_over.body,
                                          "cancelPersistedAt": cancelled_event["timestamp"], "newBoundEvents": bound_events,
                                          "goAfterCancel": released.body, "goEvents": go_events}
        report["passed"] = True
        report["verifiedCases"] = ["V1-06"]
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
