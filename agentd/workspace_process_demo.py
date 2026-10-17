"""真实agentd SIGKILL/重启验收；仅项目本地Go/gVisor/NFS与固定Replay。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from collections import Counter
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
from .app.runtime.session import _branchable_flags
from .workspace_demo import REPO, checked, observe_pod, ready, start_go, stop_go

CONTENT = "synthetic process recovery report\n"
API = "http://127.0.0.1:18090"
TERMINAL = {"succeeded", "failed", "cancelled", "limit_exceeded", "interrupted"}


def entries(path: Path, *, go_format: bool = False) -> list[dict]:
    """只读监测已出现的完整JSONL；不抢活动服务的写锁或修改控制文件。"""
    if not path.exists():
        return []
    result = []
    for raw in path.read_bytes().splitlines(keepends=True):
        if not raw.endswith(b"\n"):
            break
        row = json.loads(raw)
        digest = row.pop("sha256")
        if go_format:
            # Go按struct字段顺序编码，checksum是最后字段；不能用Python排序重新编码。
            marker = raw.rfind(b',"sha256":')
            assert marker > 0
            encoded = raw[:marker] + b"}"
        else:
            encoded = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        assert digest == hashlib.sha256(encoded).hexdigest()
        assert row["seq"] == len(result) + 1
        result.append(row)
    return result


def state_for(path: Path, task_id: str) -> dict | None:
    return next((e["data"] for e in reversed(entries(path)) if e["type"] == "task.state" and e["data"]["taskId"] == task_id), None)


def start_agent(root: Path, go_token: str, api_token: str, alert_token: str, number: int, replay_path: Path | None = None, *, port: int = 18090):
    if port not in {18090, 18091}:
        raise ValueError("process fixture only uses ports 18090/18091")
    # 清除继承的模型/路由/Operator配置。子进程只拿本次合成Agent身份，不拿Operator Token。
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTD_", "SANDBOXD_"))
           and k not in {"TYPESAFE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"}}
    env.update({"AGENTD_TOKEN": api_token, "AGENTD_ALERT_TOKEN": alert_token, "SANDBOXD_TOKEN": go_token,
                "AGENTD_LLM_MODE": "replay", "AGENTD_ROUTER_MODE": "off", "AGENTD_REPLAY_FILE": str(replay_path or root / "replay.json"),
                "AGENTD_RECOVERY_DIR": str(root / "agent-control"), "AGENTD_TRACE_DIR": str(root / "legacy-traces"),
                "AGENTD_SANDBOXD_URL": "http://127.0.0.1:10880", "AGENTD_PROMETHEUS_URL": "http://127.0.0.1:19090"})
    # 故意不传WORKSPACE_BACKEND，验证部署入口的nfs默认值。
    log_path = root / ("agentd-process-" + str(number) + ".log")
    log = log_path.open("w")
    log_path.chmod(0o600)
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "agentd.app.main:create_app", "--factory",
                                "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning", "--no-access-log"],
                               cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    print(json.dumps({"stage": "agentd-process-started", "number": number, "pid": process.pid}), flush=True)
    return process, log


def stop_agent(process, log):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    log.close()


async def agent_ready(process):
    async with httpx.AsyncClient() as client:
        async with asyncio.timeout(90):
            while True:
                if process.poll() is not None:
                    raise RuntimeError("agentd exited; inspect this run's private process log")
                try:
                    if (await client.get(API + "/readyz", timeout=1)).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)


async def run(output: Path, binary: Path, nfs_server: str) -> dict:
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nodes = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "get", "nodes", "-o", "json"]))["items"]
    assert len(nodes) == 1 and nodes[0]["metadata"]["name"] == "sandboxd-control-plane"
    server = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert nfs_server in [n["IPAddress"] for n in server["NetworkSettings"]["Networks"].values()]
    root = Path.home() / ".local/share/sandboxd/workspace-agent-smoke" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    replay = {"kind": "deterministic-policy-case", "model": "workspace-process-recovery", "responses": [
        {"toolCalls": [{"id": "write-before-kill", "name": "write_file", "args": {"path": "report.md", "content": CONTENT}}]},
        {"delayMs": 30_000, "toolCalls": [{"id": "unreturned-response", "name": "read_file", "args": {"path": "report.md"}}]},
        {"toolCalls": [{"id": "read-after-restart", "name": "read_file", "args": {"path": "report.md"}}]},
        {"delayMs": 8_000, "content": '{"summary":"Recovered process read original NFS report","severity":"info","recommendation":"Replay local-service evidence only"}'},
    ]}
    (root / "replay.json").write_text(json.dumps(replay))
    (root / "replay.json").chmod(0o600)
    journal = root / "agent-control/tasks.jsonl"
    api_token, alert_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    go, go_log, go_token = start_go(root, binary, nfs_server)
    sdk = SandboxdClient("http://127.0.0.1:10880", go_token)
    api = httpx.AsyncClient(base_url=API, headers={"Authorization": "Bearer " + api_token}, timeout=5)
    processes = []
    requests = []
    report = {"mode": "real-agentd-process-sigkill-replay", "scope": "V1-03 process restart and V1-09 old-node rejection; terminal success/cancel checks are partial V1-05",
              "controlRoot": str(root), "externalModelCalls": 0, "manualResumeCalls": 0,
              "configuration": {"workspaceBackend": "deployment default nfs", "llmMode": "replay", "routerMode": "off",
                                "replaySha256": hashlib.sha256((root / "replay.json").read_bytes()).hexdigest()},
              "apiRequests": requests, "passed": False}

    async def request(method, path, **kwargs):
        response = await api.request(method, path, **kwargs)
        requests.append({"method": method, "path": path, "statusCode": response.status_code})
        return response

    try:
        await ready(go)
        first, log = start_agent(root, go_token, api_token, alert_token, 1)
        processes.append((first, log))
        await agent_ready(first)
        created = await request("POST", "/api/v1/tasks", json={"summary": "Synthetic SIGKILL recovery", "labels": {"cluster": "local-kind", "namespace": "sandboxd-target"}})
        assert created.status_code == 202
        task_id = created.json()["taskId"]
        async with asyncio.timeout(65):
            while True:
                live = await request("GET", "/api/v1/tasks/" + task_id)
                assert live.status_code == 200 and live.json()["status"] not in TERMINAL
                state = state_for(journal, task_id)
                if state and sum(c["kind"] == "model" for c in state["calls"].values()) == 2 and any(c["kind"] == "model" and c["state"] == "reserved" for c in state["calls"].values()):
                    break
                await asyncio.sleep(0.1)
        metadata = await sdk.get_workspace(state["workspaceId"])
        assert metadata.status_code == 200 and metadata.body["phase"] == "bound"
        before_pod = observe_pod(metadata.body)
        # 队列中再放一个已取消Task，重启后它不应被调度。
        cancelled = await request("POST", "/api/v1/tasks", json={"summary": "cancelled queued fixture", "labels": {"case": "cancelled-before-restart"}})
        assert cancelled.status_code == 202
        cancelled_id = cancelled.json()["taskId"]
        assert (await request("POST", "/api/v1/tasks/" + cancelled_id + "/cancel")).status_code == 202
        before = state_for(journal, task_id)
        assert sum(c["kind"] == "model" for c in before["calls"].values()) == 2
        uncertain = [key for key, c in before["calls"].items() if c["kind"] == "model" and c["state"] == "reserved"]
        assert len(uncertain) == 1 and before["completeNodeId"]
        session_path = root / "agent-control/sessions" / (before["sessionId"] + ".jsonl")
        session_before = session_path.read_bytes()
        assert (await request("GET", "/api/v1/tasks/" + task_id)).json()["status"] == "running"
        kill_started = time.perf_counter()
        killed_at = datetime.now(timezone.utc)
        first.kill()
        assert first.wait(timeout=8) == -signal.SIGKILL
        report.update({"stateBeforeKill": before, "podBeforeKill": before_pod,
                       "kill": {"pid": first.pid, "signal": "SIGKILL", "returnCode": first.returncode,
                                "timestamp": killed_at.isoformat(), "afterCompleteNodeId": before["completeNodeId"]}})
        print(json.dumps({"stage": "sigkill-confirmed", "taskId": task_id, "returnCode": first.returncode}), flush=True)
        second, log = start_agent(root, go_token, api_token, alert_token, 2)
        processes.append((second, log))
        await agent_ready(second)
        report["killToAgentReadyMs"] = round((time.perf_counter() - kill_started) * 1000, 3)
        async with asyncio.timeout(85):
            while True:
                live = await request("GET", "/api/v1/tasks/" + task_id)
                assert live.status_code == 200
                if live.json()["status"] in TERMINAL:
                    raise AssertionError("task ended before replacement Pod observation: " + str(live.json().get("error")))
                current = state_for(journal, task_id)
                metadata = await sdk.get_workspace(before["workspaceId"])
                if (current and len(current["attempts"]) == 2 and metadata.status_code == 200 and metadata.body["phase"] == "bound"
                        and metadata.body["owner"]["attemptId"] != before["attempts"][0]["attemptId"]):
                    break
                await asyncio.sleep(0.1)
        report["killToReplacementPodObservedMs"] = round((time.perf_counter() - kill_started) * 1000, 3)
        after_pod = observe_pod(metadata.body)
        assert after_pod["podUid"] != before_pod["podUid"] and after_pod["imageId"] == before_pod["imageId"]
        async with asyncio.timeout(30):
            while True:
                response = await request("GET", "/api/v1/tasks/" + task_id)
                task = response.json()
                if task["status"] in TERMINAL:
                    break
                await asyncio.sleep(0.1)
        assert task["status"] == "succeeded", task.get("error")
        after = state_for(journal, task_id)
        for key in ("taskId", "sessionId", "branchId", "workspaceId", "deadline", "limits", "timing"):
            assert after[key] == before[key], key
        assert len(after["attempts"]) == 2 and all(a["ownerStopped"] for a in after["attempts"])
        assert set(before["calls"]) <= set(after["calls"])
        assert after["calls"][uncertain[0]]["state"] == "uncertain"
        assert sum(c["kind"] == "model" for c in after["calls"].values()) == 4
        assert sum(c["operation"] == "write_file" for c in after["calls"].values()) == 1
        assert datetime.fromisoformat(after["deadline"]) > datetime.now(timezone.utc)
        assert session_path.read_bytes().startswith(session_before)
        traces = (await request("GET", "/api/v1/tasks/" + task_id + "/traces")).json()["attempts"]
        assert len(traces) == 2 and traces[0]["mode"] == "recovery" and traces[-1]["status"] == "succeeded"
        assert traces[0]["events"][0]["details"]["usageKnown"] is False
        read = next(json.loads(s["observation"])["body"] for s in traces[-1]["steps"] if s["tool"] == "read_file")
        expected_hash = hashlib.sha256(CONTENT.encode()).hexdigest()
        assert read["content"] == CONTENT and read["sha256"] == expected_hash
        server_hash = checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum",
                               "/export/workspaces/" + after["workspaceId"] + "/report.md"]).split()[0]
        assert server_hash == expected_hash
        tree = (await request("GET", "/api/v1/sessions/" + after["sessionId"] + "/tree")).json()
        path = (await request("GET", "/api/v1/sessions/" + after["sessionId"] + "/path/" + tree["activeLeafId"])).json()
        messages = path["messages"]
        assert _branchable_flags(messages)[-1]
        calls = Counter(c["id"] for m in messages if m["role"] == "assistant" for c in m.get("toolCalls", []))
        results = Counter(m["toolCallId"] for m in messages if m["role"] == "tool")
        assert calls == results and all(v == 1 for v in calls.values())
        old = before["completeNodeId"]
        old_path = await request("GET", "/api/v1/sessions/" + after["sessionId"] + "/path/" + old)
        assert old_path.status_code == 200
        rejected = await request("POST", "/api/v1/sessions/" + after["sessionId"] + "/branch/" + old)
        assert rejected.status_code == 409 and "历史文件版本" in rejected.json()["detail"]
        assert (await request("GET", "/api/v1/sessions/" + after["sessionId"] + "/tree")).json()["activeLeafId"] == tree["activeLeafId"]
        report.update({"stateAfterRestart": after, "podAfterRestart": after_pod, "traces": traces,
                       "sessionPrefixPreserved": True, "sessionPrefixSha256BeforeKill": hashlib.sha256(session_before).hexdigest(),
                       "visibleToolCallIds": list(calls), "serverFileSha256": server_hash,
                       "oldNodeRequest": {"nodeId": old, "pathStatus": old_path.status_code, "branchStatus": rejected.status_code},
                       "killToTaskSucceededMs": round((time.perf_counter() - kill_started) * 1000, 3)})
        released = [e["data"] for e in entries(root / "go-control/workspaces.jsonl", go_format=True)
                    if e["type"] == "workspace.state" and e["data"]["owner"].get("attemptId") == before["attempts"][0]["attemptId"]
                    and e["data"]["phase"] == "released"]
        assert released and released[-1]["owner"]["stopConfirmed"]
        report["previousGoRelease"] = released[-1]
        # 再启动一次，只检查终态不复活；不手动resume或创建额外执行。
        stable = journal.read_bytes()
        stop_agent(second, processes[-1][1])
        third, log = start_agent(root, go_token, api_token, alert_token, 3)
        processes.append((third, log))
        await agent_ready(third)
        await asyncio.sleep(0.3)
        assert (await request("GET", "/api/v1/tasks/" + task_id)).json()["status"] == "succeeded"
        assert (await request("GET", "/api/v1/tasks/" + cancelled_id)).json()["status"] == "cancelled"
        assert journal.read_bytes() == stable
        assert state_for(journal, cancelled_id)["attempts"] == []
        report["terminalRestart"] = {"succeededTaskId": task_id, "cancelledTaskId": cancelled_id, "journalUnchanged": True,
                                     "cancelledAttemptCount": 0, "scope": "success/cancel only; failed/limit not tested here"}
        report["manualResumeCalls"] = sum(r["method"] == "POST" and r["path"].endswith("/resume") for r in requests)
        assert report["manualResumeCalls"] == 0
        report["passed"] = True
        report["verifiedCases"] = ["V1-03", "V1-09"]
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": public_error(exc)}
        raise
    finally:
        for process, log in reversed(processes):
            stop_agent(process, log)
        # 只有本次子进程均退出、文件锁释放后才管理对账，绝不并发追加同一日志。
        if journal.exists():
            ledger = None
            try:
                ledger = RecoveryLedger(journal)
                runner = AgentRunner(object(), sdk, ReplayModelGateway(root / "replay.json"))
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
    print(json.dumps({"passed": result["passed"], "output": str(args.output), "verifiedCases": result.get("verifiedCases", [])}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
