"""通过统一入口实际启动Go/agentd，强杀自己的agentd再接续；固定Replay。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import signal
import subprocess
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
from .workspace_demo import REPO, checked, observe_pod
from .workspace_process_demo import state_for, stop_agent, TERMINAL

CONTENT = "synthetic unified-entry recovery\n"


def start(root, action, env, number):
    path = root / (action + "-" + str(number) + ".log")
    log = path.open("a")
    path.chmod(0o600)
    child = subprocess.Popen(["bash", "hack/workspace.sh", action], cwd=REPO, env=env,
                             stdout=log, stderr=subprocess.STDOUT)
    print(json.dumps({"stage": "entry-started", "action": action, "number": number, "pid": child.pid}), flush=True)
    return child, log


async def wait_ready(process, url, path, timeout):
    async with httpx.AsyncClient() as client:
        async with asyncio.timeout(timeout):
            while True:
                if process.poll() is not None:
                    raise RuntimeError("entry child exited; inspect private synthetic run log")
                try:
                    if (await client.get(url + path, timeout=1)).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.1)


async def run(output: Path):
    root = Path.home() / ".local/share/sandboxd/workspace-agent-smoke" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    replay = root / "replay.json"
    replay.write_text(json.dumps({"kind": "deterministic-policy-case", "responses": [
        {"toolCalls": [{"id": "write-entry", "name": "write_file", "args": {"path": "entry.md", "content": CONTENT}}]},
        {"delayMs": 60000, "toolCalls": [{"id": "unreturned-entry-read", "name": "read_file", "args": {"path": "entry.md"}}]},
        {"toolCalls": [{"id": "read-entry-after-restart", "name": "read_file", "args": {"path": "entry.md"}}]},
        {"delayMs": 8000, "content": '{"summary":"Recovered via unified entry","recommendation":"observe"}'}]}))
    replay.chmod(0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTD_", "SANDBOXD_"))
           and k not in {"OPENAI_API_KEY", "DEEPSEEK_API_KEY", "TYPESAFE_API_KEY"}}
    go_token, operator, api_token, alert_token = [secrets.token_urlsafe(32) for _ in range(4)]
    env.update(SANDBOXD_TOKEN=go_token, SANDBOXD_OPERATOR_TOKEN=operator,
               SANDBOXD_WORKSPACE_CONTROL_DIR=str(root / "go-control"), AGENTD_TOKEN=api_token,
               AGENTD_ALERT_TOKEN=alert_token, AGENTD_RECOVERY_DIR=str(root / "agent-control"),
               AGENTD_TRACE_DIR=str(root / "legacy-traces"), AGENTD_REPLAY_FILE=str(replay),
               AGENTD_LLM_MODE="replay", AGENTD_SANDBOXD_URL="http://127.0.0.1:8080")
    sdk = SandboxdClient("http://127.0.0.1:8080", go_token)
    api = httpx.AsyncClient(base_url="http://127.0.0.1:8090", headers={"Authorization": "Bearer " + api_token}, timeout=5)
    children, go, task_id = [], None, None
    report = {"mode": "real-unified-entry-sigkill-replay", "passed": False, "externalModelCalls": 0,
              "controlRoot": str(root), "entryScriptSha256": hashlib.sha256((REPO / "hack/workspace.sh").read_bytes()).hexdigest(),
              "binarySha256": hashlib.sha256((REPO / ".cache/workspace-sandboxd").read_bytes()).hexdigest(),
              "replaySha256": hashlib.sha256(replay.read_bytes()).hexdigest()}
    journal = root / "agent-control/tasks.jsonl"
    try:
        started = time.perf_counter()
        go, log = start(root, "go", env, 1)
        children.append((go, log))
        await wait_ready(go, "http://127.0.0.1:8080", "/readyz", 40)
        report["goReadyMs"] = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        first, log = start(root, "agent", env, 1)
        children.append((first, log))
        await wait_ready(first, "http://127.0.0.1:8090", "/healthz", 100)
        report["initialAgentReadyMs"] = (time.perf_counter() - started) * 1000
        # 只检验本夹具生成的合成环境字段，不输出任何环境值。
        names = {v.split(b"=", 1)[0] for v in Path(f"/proc/{first.pid}/environ").read_bytes().split(b"\x00") if v}
        assert b"SANDBOXD_OPERATOR_TOKEN" not in names
        report["operatorEnvironmentRemoved"] = True
        response = await api.post("/api/v1/tasks", json={"summary": "synthetic entry verification", "labels": {"cluster": "local-kind"}})
        assert response.status_code == 202
        task_id = response.json()["taskId"]
        async with asyncio.timeout(75):
            while True:
                before = state_for(journal, task_id)
                if before and sum(c["kind"] == "model" for c in before["calls"].values()) == 2:
                    break
                await asyncio.sleep(.1)
        old = await sdk.get_workspace(before["workspaceId"])
        old_pod = observe_pod(old.body)
        prefix_path = root / "agent-control/sessions" / (before["sessionId"] + ".jsonl")
        prefix = prefix_path.read_bytes()
        killed = time.perf_counter()
        first.send_signal(signal.SIGKILL)
        assert first.wait(timeout=8) == -9
        second, log = start(root, "agent", env, 2)
        children.append((second, log))
        await wait_ready(second, "http://127.0.0.1:8090", "/healthz", 100)
        report["killToApiReadyMs"] = (time.perf_counter() - killed) * 1000
        replacement, read_seen = None, None
        async with asyncio.timeout(90):
            while True:
                response = await api.get("/api/v1/tasks/" + task_id)
                current = response.json()
                state = state_for(journal, task_id)
                if replacement is None:
                    metadata = await sdk.get_workspace(before["workspaceId"])
                    if metadata.body.get("phase") == "bound" and metadata.body["owner"]["podUid"] != old.body["owner"]["podUid"]:
                        report["killToReplacementObservedMs"] = (time.perf_counter() - killed) * 1000
                        replacement = observe_pod(metadata.body)
                if read_seen is None and any(c["kind"] == "tool" and c["state"] == "finished" and
                        c.get("operation") == "read_file" for c in state["calls"].values()):
                    read_seen = (time.perf_counter() - killed) * 1000
                if current["status"] in TERMINAL:
                    break
                await asyncio.sleep(.1)
        assert current["status"] == "succeeded", current.get("error")
        assert replacement is not None
        after = state_for(journal, task_id)
        for key in ("taskId", "sessionId", "branchId", "workspaceId", "limits", "deadline", "timing", "provider"):
            assert before[key] == after[key], key
        assert len(after["attempts"]) == 2 and all(a["ownerStopped"] for a in after["attempts"])
        calls = list(after["calls"].values())
        assert sum(c["kind"] == "model" for c in calls) == 4
        assert sum(c.get("operation") == "write_file" for c in calls) == 1
        traces = (await api.get("/api/v1/tasks/" + task_id + "/traces")).json()["attempts"]
        assert isinstance(traces, list)
        read = next(s for t in traces for s in t["steps"] if s["tool"] == "read_file")
        actual = json.loads(read["observation"])["body"]["sha256"]
        expected = hashlib.sha256(CONTENT.encode()).hexdigest()
        assert actual == expected and prefix_path.read_bytes().startswith(prefix)
        server_hash = checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum",
                               "/export/workspaces/" + after["workspaceId"] + "/entry.md"]).split()[0]
        assert server_hash == expected
        report.update(passed=True, stateBeforeKill=before, stateAfterRestart=after, oldPod=old_pod, newPod=replacement,
                      traces=traces, killToReadObservedMs=read_seen, killToSucceededMs=(time.perf_counter() - killed) * 1000,
                      serverSha256=server_hash, sessionPrefixPreserved=True,
                      timingScope="single sample; source mount import and deliberate Replay delay included; not production RTO")
    except Exception as exc:
        report["error"] = public_error(exc)
        raise
    finally:
        for child, log in reversed(children):
            if child is go:
                continue
            stop_agent(child, log)
        try:
            if journal.exists():
                ledger = RecoveryLedger(journal)
                try:
                    runner = AgentRunner(object(), sdk, ReplayModelGateway(replay))
                    for state in ledger.records():
                        if state["attempts"] and not state["attempts"][-1]["ownerStopped"]:
                            await runner.stop_attempt(RecoveryAttempt(ledger, state["taskId"], state["attempts"][-1]["attemptId"]))
                finally:
                    ledger.close()
        except Exception as exc:
            report["cleanupError"] = public_error(exc)
            report["passed"] = False
        if go is not None:
            stop_agent(*children[0])
        await api.aclose()
        await sdk.close()
        report["timestamp"] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    assert report["passed"], report.get("cleanupError")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output))
    print(json.dumps({"passed": result["passed"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
