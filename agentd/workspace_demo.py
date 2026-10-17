"""真实本地 Go/gVisor/NFS + Replay Agent 文件链路；无付费模型调用。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from .app.clients import SandboxdClient
from .app.model_gateway import ReplayModelGateway
from .app.models import AlertEvent
from .app.runner import AgentRunner
from .app.store import TaskStore
from .app.runtime.recovery import RecoveryLedger
from .app.runtime.execution import RecoveryAttempt

CONTENT = "synthetic Agent report on durable NFS\n"
REPO = Path(__file__).resolve().parents[1]


def checked(argv: list[str]) -> str:
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=35).stdout


def observe_pod(record: dict) -> dict:
    """管理夹具验证真实Pod/NFS；不会向Agent暴露这些管理员命令。"""
    workspace = record["workspaceId"]
    pod_name = record["owner"]["podName"]
    k = ["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-demo"]
    pod = json.loads(checked([*k, "get", "pod", pod_name, "-o", "json"]))
    assert pod["metadata"]["uid"] == record["owner"]["podUid"]
    assert pod["spec"]["runtimeClassName"] == "gvisor"
    assert pod["spec"]["securityContext"]["runAsUser"] == 65532
    dmesg = checked([*k, "exec", pod_name, "--", "dmesg"])
    assert "gVisor" in dmesg
    mount_path = "/var/lib/kubelet/pods/" + pod["metadata"]["uid"] + "/volumes/kubernetes.io~nfs/sandboxd-" + workspace
    mount = json.loads(checked(["docker", "exec", "sandboxd-control-plane", "findmnt", "--json", "--target", mount_path,
                                "--output", "SOURCE,FSTYPE,OPTIONS"]))["filesystems"][0]
    assert mount["fstype"] == "nfs4" and "vers=4.1" in mount["options"]
    return {"workspaceId": workspace, "attemptId": record["owner"]["attemptId"], "podUid": pod["metadata"]["uid"],
            "sandboxId": record["owner"]["sandboxId"], "runtimeClass": "gvisor", "runAsUser": 65532,
            "imageId": record["imageId"], "imageRef": record["imageRef"], "nfsMount": mount,
            "gvisorStartup": next(line for line in dmesg.splitlines() if "gVisor" in line)}


class ObservedClient(SandboxdClient):
    """管理员侧采集组件证据；Agent 工具仍只走受限 HTTP，不得到 exec 能力。"""
    def __init__(self, token: str, *, lose_write_ack: bool = False) -> None:
        super().__init__("http://127.0.0.1:10880", token)
        self.observations = []
        self.lose_write_ack = lose_write_ack
        self.ack_losses = []
        self.rpc_timings = []

    async def claim_workspace(self, workspace, task, attempt):
        started = time.perf_counter()
        result = await super().claim_workspace(workspace, task, attempt)
        self.rpc_timings.append({"event": "claim", "attemptId": attempt, "statusCode": result.status_code,
                                 "completedMonotonic": time.perf_counter(), "elapsedMs": (time.perf_counter() - started) * 1000})
        if 200 <= result.status_code < 300:
            self.observations.append(observe_pod(result.body))
        return result

    async def release_workspace(self, binding):
        started = time.perf_counter()
        result = await super().release_workspace(binding)
        self.rpc_timings.append({"event": "release", "attemptId": binding["attemptId"], "statusCode": result.status_code,
                                 "completedMonotonic": time.perf_counter(), "elapsedMs": (time.perf_counter() - started) * 1000,
                                 "stopConfirmed": isinstance(result.body, dict) and result.body.get("owner", {}).get("stopConfirmed") is True})
        return result

    async def workspace_files(self, binding, operation):
        started = time.perf_counter()
        result = await super().workspace_files(binding, operation)
        self.rpc_timings.append({"event": "file", "operation": operation["operation"], "attemptId": binding["attemptId"],
                                 "statusCode": result.status_code, "completedMonotonic": time.perf_counter(),
                                 "elapsedMs": (time.perf_counter() - started) * 1000})
        if self.lose_write_ack and operation["operation"] == "write_file" and 200 <= result.status_code < 300:
            self.lose_write_ack = False
            self.ack_losses.append({"kind": "discard-confirmation-after-real-RPC", "workspaceId": binding["workspaceId"],
                                    "attemptId": binding["attemptId"], "path": operation["path"],
                                    "returnedHash": result.body["newSha256"]})
            raise httpx.ReadTimeout("fixture discarded confirmation after actual service write")
        return result


def start_go(root: Path, binary: Path, nfs_server: str):
    agent_token = secrets.token_urlsafe(32)
    child_env = dict(os.environ, SANDBOXD_TOKEN=agent_token, SANDBOXD_OPERATOR_TOKEN=secrets.token_urlsafe(32))
    log_path = root / "sandboxd.log"
    log = log_path.open("a")
    log_path.chmod(0o600)
    process = subprocess.Popen([str(binary), "-listen", "127.0.0.1:10880", "-pool-size", "0",
                                "-workspace-nfs-server", nfs_server, "-workspace-image", "sandboxd-workspace:runtime-v1.5",
                                "-workspace-control-dir", str(root / "go-control")],
                               env=child_env, stdout=log, stderr=subprocess.STDOUT)
    return process, log, agent_token


async def ready(process):
    async with httpx.AsyncClient() as client:
        async with asyncio.timeout(30):
            while True:
                if process.poll() is not None:
                    raise RuntimeError("project Go service exited; inspect private synthetic run log")
                try:
                    if (await client.get("http://127.0.0.1:10880/readyz", timeout=1)).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)


def stop_go(process, log):
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
    log.close()


async def reconcile(root: Path, output: Path, binary: Path, nfs_server: str):
    # 管理员只能指定本夹具留下的一个run；不提供自由资源删除或文件清理。
    import re
    expected = Path.home() / ".local/share/sandboxd/workspace-agent-smoke"
    if root.resolve().parent != expected.resolve() or not re.fullmatch(r"[a-f0-9]{32}", root.name):
        raise ValueError("requires this fixture's retained native control root")
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    process, log, token = start_go(root, binary, nfs_server)
    client = SandboxdClient("http://127.0.0.1:10880", token)
    ledger = RecoveryLedger(root / "agent-control/tasks.jsonl")
    report = {"mode": "real-local-administrative-reconciliation", "externalModelCalls": 0,
              "controlRoot": str(root), "observations": []}
    try:
        await ready(process)
        runner = AgentRunner(object(), client, ReplayModelGateway(root / "replay.json"))
        for state in ledger.records():
            if not state["attempts"] or state["attempts"][-1]["ownerStopped"]:
                continue
            before = await client.get_workspace(state["workspaceId"])
            await runner.stop_attempt(RecoveryAttempt(ledger, state["taskId"], state["attempts"][-1]["attemptId"]))
            after = await client.get_workspace(state["workspaceId"])
            report["observations"].append({"taskId": state["taskId"], "before": before.body, "after": after.body,
                                           "ownerStopped": ledger.get(state["taskId"])["attempts"][-1]["ownerStopped"]})
        report["passed"] = True
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return report
    finally:
        await client.close()
        ledger.close()
        stop_go(process, log)


async def run(output: Path, binary: Path, nfs_server: str, fault_after_first_turn: bool = False, lose_write_ack: bool = False) -> dict:
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nodes = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "get", "nodes", "-o", "json"]))["items"]
    assert len(nodes) == 1 and nodes[0]["metadata"]["name"] == "sandboxd-control-plane"
    server = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert nfs_server in [n["IPAddress"] for n in server["NetworkSettings"]["Networks"].values()]
    run_id = uuid4().hex
    root = Path.home() / ".local/share/sandboxd/workspace-agent-smoke" / run_id
    root.mkdir(parents=True, mode=0o700)
    script = root / "replay.json"
    responses = [
        {"toolCalls": [{"id": "write-report", "name": "write_file", "args": {"path": "report.md", "content": CONTENT}}]},
        {"toolCalls": [{"id": "read-report", "name": "read_file", "args": {"path": "report.md"}}]},
        {"content": '{"summary":"Synthetic report written and read through the workspace tool","severity":"info","recommendation":"This is a local replay demonstration."}'},
    ]
    if fault_after_first_turn:
        responses.insert(2, {"toolCalls": [{"id": "read-after-recovery", "name": "read_file", "args": {"path": "report.md"}}]})
    script.write_text(json.dumps({"kind": "deterministic-policy-case", "model": "workspace-file-smoke", "responses": responses}))
    script.chmod(0o600)
    # 仅管理夹具为Go子进程生成两个不同的临时合成Token；Runner只收到Agent Token。
    process, log, agent_token = start_go(root, binary, nfs_server)
    client = ObservedClient(agent_token, lose_write_ack=lose_write_ack)
    store = TaskStore(root / "legacy-traces", recovery_dir=root / "agent-control")
    faults = []
    if fault_after_first_turn:
        original_boundary = store._ledger.complete_boundary
        def boundary(task_id, attempt_id, node_id, command_ids):
            state = original_boundary(task_id, attempt_id, node_id, command_ids)
            wrote = any(c["kind"] == "tool" and c["operation"] == "write_file" and c["state"] == "finished"
                        for c in state["calls"].values())
            if wrote and not faults:
                owner = state["attempts"][-1]
                pod_name = "sandbox-" + owner["sandboxId"]
                k = ["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-demo"]
                pod = json.loads(checked([*k, "get", "pod", pod_name, "-o", "json"]))
                assert pod["metadata"]["uid"] == owner["podUid"]
                assert "sandbox.io/workspace-writer" in pod["metadata"]["finalizers"]
                requested_monotonic = time.perf_counter()
                checked([*k, "delete", "pod", pod_name, "--wait=false", "--grace-period=1"])
                faults.append({"kind": "administrative-normal-pod-delete", "afterCompleteNodeId": node_id,
                               "taskId": task_id, "attemptId": attempt_id, "podUid": owner["podUid"],
                               "timestamp": datetime.now(timezone.utc).isoformat(), "requestedMonotonic": requested_monotonic})
            return state
        store._ledger.complete_boundary = boundary
    worker = None
    started = time.perf_counter()
    try:
        await ready(process)
        runner = AgentRunner(object(), client, ReplayModelGateway(script))
        task = await store.enqueue(AlertEvent(labels={"cluster": "local-kind", "namespace": "sandboxd-target"},
                                              annotations={"summary": "Synthetic durable workspace file smoke"}))
        worker = asyncio.create_task(store.worker(runner))
        async with asyncio.timeout(330):
            while True:
                task = await store.get(task.task_id)
                if task.status in {"succeeded", "failed", "cancelled", "limit_exceeded", "interrupted"}:
                    break
                await asyncio.sleep(0.1)
        assert task.status == "succeeded", task.error
        traces = await store.get_task_traces(task.task_id)
        assert len(traces) == (2 if fault_after_first_turn or lose_write_ack else 1) and traces[-1]["status"] == "succeeded"
        steps = [step for trace in traces for step in trace["steps"]]
        assert [s["tool"] for s in steps] == (["read_file", "read_file"] if lose_write_ack else ["write_file", "read_file"])
        if lose_write_ack:
            assert steps[0]["pluginId"] == "workspace-recovery"
            assert json.loads(steps[0]["observation"])["matchesExpectedWrite"] is True
        read = json.loads(steps[-1]["observation"])["body"]
        expected = hashlib.sha256(CONTENT.encode()).hexdigest()
        assert read["content"] == CONTENT and read["sha256"] == expected
        server_hash = checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum",
                               "/export/workspaces/" + task.workspace_id + "/report.md"]).split()[0]
        assert server_hash == expected
        state = store._ledger.get(task.task_id)
        assert state["status"] == "succeeded" and state["attempts"][-1]["ownerStopped"]
        metadata = await client.get_workspace(task.workspace_id)
        assert metadata.status_code == 200 and metadata.body["phase"] == "released"
        assert metadata.body["owner"]["stopConfirmed"]
        # 元数据确认后，还检查相同UID的业务Pod已由Go正常释放。
        deletion_started = time.perf_counter()
        async with asyncio.timeout(10):
            while True:
                pods = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-demo", "get", "pods", "-o", "json"]))["items"]
                if all(p["metadata"]["uid"] != state["attempts"][-1]["podUid"] for p in pods):
                    break
                # Go释放归属后，API对象的自然删除可稍后可见；不强删或清空finalizer。
                await asyncio.sleep(0.1)
        deletion_wait_ms = round((time.perf_counter() - deletion_started) * 1000, 3)
        if fault_after_first_turn:
            assert len(faults) == 1 and len(state["attempts"]) == 2
            assert all(a["ownerStopped"] for a in state["attempts"])
            assert state["attempts"][0]["podUid"] != state["attempts"][1]["podUid"]
            assert all(o["workspaceId"] == task.workspace_id for o in client.observations)
            assert traces[0]["status"] == "interrupted"
            assert traces[0]["taskId"] == traces[1]["taskId"] == task.task_id
        if lose_write_ack:
            assert len(client.ack_losses) == 1 and client.ack_losses[0]["returnedHash"] == expected
            assert len(state["attempts"]) == 2 and all(a["ownerStopped"] for a in state["attempts"])
            assert sum(c["operation"] == "write_file" for c in state["calls"].values()) == 1
            assert any(c["operation"] == "write_file" and c["state"] == "uncertain" for c in state["calls"].values())
            assert any(e["type"] == "workspace.write.inspected" and e["details"]["matchesExpectedWrite"] for e in traces[-1]["events"])
            visible = await store._journals[task.task_id].active_path()
            assert not any(c["id"] == "write-report" for n in visible for c in n["message"].get("toolCalls", []))
        report = {"timestamp": datetime.now(timezone.utc).isoformat(), "mode": "real-local-services-replay-agent",
                  "scope": ("V1-07 actual service write then discarded caller confirmation; client fault injection, not TCP fault"
                            if lose_write_ack else "V1-02 automatic same-Task continuation after normal Pod deletion at a complete tool boundary"
                            if fault_after_first_turn else "V1-01 Agent file write/read via real Go/gVisor/NFS; not process-crash or Pod-fault acceptance"),
                  "passed": True, "externalModelCalls": 0, "runId": run_id,
                  "controlRoot": str(root), "task": task.model_dump(mode="json", by_alias=True, exclude={"trace"}),
                  "workspaceObservations": client.observations, "trace": traces[-1], "traces": traces,
                  "faults": faults, "calls": state["calls"], "timing": state.get("timing"), "deadline": state["deadline"],
                  "acknowledgmentLosses": client.ack_losses,
                  "workspaceAfterRelease": metadata.body,
                  "podDisappearanceWaitMs": deletion_wait_ms,
                  "serverFileSha256AfterRelease": server_hash, "expectedSha256": expected,
                  "elapsedMs": round((time.perf_counter() - started) * 1000, 3)}
        if fault_after_first_turn:
            origin = faults[0]["requestedMonotonic"]
            timings = client.rpc_timings
            def first_ms(predicate):
                return next(((r["completedMonotonic"] - origin) * 1000 for r in timings
                             if r["completedMonotonic"] >= origin and predicate(r)), None)
            report["recoveryTiming"] = {
                "scope": "single controlled Pod deletion; monotonic observer times, includes Go/HTTP overhead; not production RTO",
                "faultToRejectedFileRpcMs": first_ms(lambda r: r["event"] == "file" and r["statusCode"] >= 400),
                "faultToOldStopConfirmedMs": first_ms(lambda r: r["event"] == "release" and r["attemptId"] == faults[0]["attemptId"] and r["stopConfirmed"]),
                "faultToNewClaimReadyMs": first_ms(lambda r: r["event"] == "claim" and r["attemptId"] != faults[0]["attemptId"] and 200 <= r["statusCode"] < 300),
                "faultToSuccessfulReadMs": first_ms(lambda r: r["event"] == "file" and r.get("operation") == "read_file" and r["attemptId"] != faults[0]["attemptId"] and r["statusCode"] == 200),
                "writeFileReservations": sum(c["operation"] == "write_file" for c in state["calls"].values()),
                "readFileReservations": sum(c["operation"] == "read_file" for c in state["calls"].values()),
                "successfulRecoveryReads": sum(r["event"] == "file" and r.get("operation") == "read_file" and r["statusCode"] == 200 and r["attemptId"] != faults[0]["attemptId"] for r in timings),
            }
            report["rpcTimings"] = timings
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return report
    finally:
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await client.close()
        store.close()
        stop_go(process, log)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=REPO / ".cache/workspace-sandboxd")
    parser.add_argument("--nfs-server", required=True)
    parser.add_argument("--reconcile-control-root", type=Path)
    fault = parser.add_mutually_exclusive_group()
    fault.add_argument("--fault-after-first-turn", action="store_true")
    fault.add_argument("--lose-write-ack", action="store_true")
    args = parser.parse_args()
    if args.reconcile_control_root is not None:
        result = asyncio.run(reconcile(args.reconcile_control_root, args.output, args.binary.resolve(), args.nfs_server))
    else:
        result = asyncio.run(run(args.output, args.binary.resolve(), args.nfs_server, args.fault_after_first_turn, args.lose_write_ack))
    print(json.dumps({"passed": result["passed"], "output": str(args.output), "mode": result["mode"]}))


if __name__ == "__main__":
    main()
