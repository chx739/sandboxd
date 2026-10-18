"""真实历史恢复窗口的Agent TCP/SIGKILL或取消；故障包装只装在本夹具子进程。"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import time
from uuid import uuid4

import httpx

from .app.clients import SandboxdClient
from .app.model_gateway import ReplayModelGateway
from .app.redaction import public_error
from .app.runner import AgentRunner
from .app.runtime.checkpoint_coordinator import CheckpointCoordinator
from .app.runtime.checkpoints import CheckpointLedger
from .app.runtime.execution import RecoveryAttempt
from .app.runtime.recovery import RecoveryLedger, new_id
from .app.runtime.restore_coordinator import RestoreCoordinator
from .app.runtime.session import _branchable_flags
from .app.runtime.session_refs import SessionReferences
from .workspace_checkpoint_fault_demo import observe_maintenance, require_idle_ports
from .workspace_demo import REPO, checked, observe_pod, ready, start_go, stop_go
from .workspace_process_demo import API, TERMINAL, agent_ready, entries, start_agent, state_for, stop_agent

PHASES = ("started", "files-ready", "before-attached", "attached", "after-tool", "cancel-ready")
NEW_CONTENT = "synthetic latest file after historical restore; do not roll back\n"


def verify_phase_clocks(before: dict, after: dict, states: list[dict]) -> None:
    """claim/loop是既有独立窗口；故障前已进入哪个窗口，就保留哪个截止时间。"""
    timing = before["timing"]
    assert after["timing"]["claimStartedAt"] == timing["claimStartedAt"]
    claim_deadline = (datetime.fromisoformat(timing["claimStartedAt"]) + timedelta(seconds=timing["claimSeconds"])).isoformat()
    assert all(s["deadline"] == claim_deadline for s in states if s["attempts"] and s["timing"]["loopStartedAt"] is None)
    if timing["loopStartedAt"] is not None:
        assert after["deadline"] == before["deadline"] and after["timing"] == timing
        assert all(s["deadline"] == before["deadline"] and s["timing"]["loopStartedAt"] == timing["loopStartedAt"]
                   for s in states if s["timing"]["loopStartedAt"] is not None)


def native_root(root: Path) -> Path:
    expected = Path.home() / ".local/share/sandboxd/workspace-restore-fault"
    if root.resolve().parent != expected.resolve() or not re.fullmatch(r"[a-f0-9]{32}", root.name):
        raise ValueError("requires this fixture's private native UUID directory")
    return root.resolve()


def atomic_json(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    with temporary.open("x") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def install_cutpoint(root: Path, phase: str):
    root = native_root(root)
    if phase not in PHASES or (root / "cut-point.json").exists():
        raise ValueError("unknown phase or reused cut point")
    triggered = False
    def mark(request: dict, *, stopped: bool = True):
        nonlocal triggered
        if triggered:return
        triggered = True
        atomic_json(root / "cut-point.json", {"phase": phase, "pid": os.getpid(),
                    **{k: request[k] for k in ("restoreId", "taskId", "sourceCheckpointId", "status")}})
        if stopped:os.kill(os.getpid(), signal.SIGSTOP)

    original_call = RestoreCoordinator._call
    async def called(coordinator, action, request, source, recovery, **kwargs):
        result = await original_call(coordinator, action, request, source, recovery, **kwargs)
        if action == "wait" and not (root / "restore-maintenance.json").exists():
            observed = observe_maintenance(result["barrier"])
            if observed:atomic_json(root / "restore-maintenance.json", observed)
        if action == "start" and phase in {"started", "cancel-ready"}:
            mark(request, stopped=phase != "cancel-ready")
            if phase == "cancel-ready":
                # 夹具在真实Start之后让事件循环保持可响应；原90秒/Task预算仍生效。
                await asyncio.wait_for(asyncio.Event().wait(), 60)
        return result
    RestoreCoordinator._call = called
    original_advance = CheckpointLedger.advance_restore
    def advanced(ledger, rid, status, failure=None):
        if phase == "before-attached" and status == "ATTACHED":mark(ledger.restore_request(rid))
        result = original_advance(ledger, rid, status, failure)
        if (phase == "files-ready" and status == "FILES_READY") or (phase == "attached" and status == "ATTACHED"):
            mark(result)
        return result
    CheckpointLedger.advance_restore = advanced
    if phase == "after-tool":
        original_save = CheckpointCoordinator.save
        async def saved(coordinator, recovery, *args, **kwargs):
            result = await original_save(coordinator, recovery, *args, **kwargs)
            request = next((r for r in coordinator.ledger.restore_requests() if r["taskId"] == recovery.task_id), None)
            if request is not None:mark(request)
            return result
        CheckpointCoordinator.save = saved


async def run(output: Path, phase: str, source_report: Path, secret: str, endpoint: str) -> dict:
    require_idle_ports()
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert nfs["State"]["Running"] and not nfs["State"]["Paused"]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    source = json.loads(source_report.read_text());assert source["passed"]
    cp = source["checkpointAtRestart"];assert cp["status"] == "COMMITTED" and cp["data"]["repositoryId"] == "repo-component"
    original = Path(source["controlRoot"]) / "agent-control"
    expected = Path.home() / ".local/share/sandboxd/workspace-checkpoint-fault"
    assert original.parent.resolve().parent == expected.resolve() and re.fullmatch(r"[a-f0-9]{32}", original.parent.name)
    original_hashes = {str(p.relative_to(original)): hashlib.sha256(p.read_bytes()).hexdigest() for p in original.rglob("*") if p.is_file()}
    root = Path.home() / ".local/share/sandboxd/workspace-restore-fault" / uuid4().hex
    root.mkdir(mode=0o700, parents=True);shutil.copytree(original, root / "agent-control")
    script = root / "replay.json"
    tool = ({"id": "restored-write-once", "name": "write_file", "args": {"path": "report.md", "content": NEW_CONTENT, "expectedSha256": source["fileSha256"]}}
            if phase == "after-tool" else {"id": "restored-read", "name": "read_file", "args": {"path": "report.md"}})
    script.write_text(json.dumps({"kind": "deterministic-policy-case", "model": "restore-fault-replay", "responses": [
        {"toolCalls": [tool]},
        {"content": '{"summary":"Restored Task continued","recommendation":"review local evidence"}'}]}));script.chmod(0o600)
    binary = REPO / ".cache/workspace-sandboxd-restore-cleanup"
    go, go_log, go_token = start_go(root, binary, nfs["NetworkSettings"]["Networks"]["kind"]["IPAddress"],
                                   backup_secret=secret, backup_minio_ip=endpoint, backup_image="sandboxd-restic:0.18.1-v2")
    sdk = SandboxdClient("http://127.0.0.1:10880", go_token)
    api_token, alert_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    api = httpx.AsyncClient(base_url=API, headers={"Authorization": "Bearer " + api_token}, timeout=5)
    journal = root / "agent-control/tasks.jsonl";cp_path = root / "agent-control/checkpoints/checkpoints.jsonl"
    processes = [];task_id = None;rid = new_id("restore")
    report = {"passed": False, "phase": phase, "mode": "real-agent-tcp-restore-window-fault", "externalModelCalls": 0,
              "controlRoot": str(root), "sourceCheckpointId": cp["checkpointId"], "restoreId": rid, "manualResumeCalls": 0,
              "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "scope": "own Agent SIGKILL and plain restart, or fixture-gated real Start then HTTP cancel; Go/storage stay running; no GC/Live"}
    try:
        await ready(go)
        first, log = start_agent(root, go_token, api_token, alert_token, 1, checkpoint_mode="restic", restore_cutpoint=phase)
        processes.append((first, log));await agent_ready(first)
        sid = cp["identity"]["sessionId"]
        created = await api.post(f"/api/v1/sessions/{sid}/checkpoints/{cp['checkpointId']}/restore/{rid}")
        assert created.status_code == 202, created.text
        task_id = created.json()["taskId"];report["taskId"] = task_id
        async with asyncio.timeout(65):
            while not (root / "cut-point.json").exists():
                if first.poll() is not None:raise RuntimeError("child exited before restore cut point")
                await asyncio.sleep(.02)
        cut = json.loads((root / "cut-point.json").read_text())
        assert cut["pid"] == first.pid and cut["taskId"] == task_id and cut["restoreId"] == rid
        if phase != "cancel-ready":
            async with asyncio.timeout(5):
                while True:
                    stopped_pid, stopped = os.waitpid(first.pid, os.WNOHANG | os.WUNTRACED)
                    if stopped_pid:
                        assert os.WIFSTOPPED(stopped) and os.WSTOPSIG(stopped) == signal.SIGSTOP;break
                    await asyncio.sleep(.01)
        before = state_for(journal, task_id);assert before and len(before["attempts"]) == 1
        assert len(before["calls"]) == (2 if phase == "after-tool" else 0)
        original_task = state_for(journal, cp["identity"]["taskId"])
        request_before = next(r["data"] for r in reversed(entries(cp_path)) if r["type"] == "checkpoint.restore" and r["data"]["restoreId"] == rid)
        barrier_response = await sdk.get_workspace_restore(before["workspaceId"])
        if phase == "after-tool":
            assert barrier_response.status_code == 404
            barrier_response = await sdk.get_workspace_backup(before["workspaceId"])
            report["businessBefore"] = observe_pod((await sdk.get_workspace(before["workspaceId"])).body)
        assert barrier_response.status_code == 200
        barrier = barrier_response.body
        maintenance_file = root / "restore-maintenance.json"
        if maintenance_file.exists():maintenance = json.loads(maintenance_file.read_text())
        else:
            maintenance = None
            async with asyncio.timeout(15):
                while maintenance is None:
                    try:maintenance = observe_maintenance(barrier)
                    except (IndexError, StopIteration, KeyError):await asyncio.sleep(.1)
        assert maintenance and maintenance["actualRuntimeType"] == "io.containerd.runsc.v1"
        report.update(cutPoint=cut, before=before, restoreBefore=request_before, barrierBefore=barrier, maintenance=maintenance)
        started = time.perf_counter()
        if phase == "cancel-ready":
            cancelled = await api.post("/api/v1/tasks/" + task_id + "/cancel")
            assert cancelled.status_code == 202, cancelled.text
            report["cancelAccepted"] = True
        else:
            first.kill();assert first.wait(timeout=8) == -signal.SIGKILL
            report["kill"] = {"signal": "SIGKILL", "returnCode": first.returncode, "osStopConfirmed": True}
            second, log = start_agent(root, go_token, api_token, alert_token, 2, checkpoint_mode="restic")
            processes.append((second, log));await agent_ready(second)
            report["killToReadyMs"] = (time.perf_counter() - started) * 1000
        business = None
        async with asyncio.timeout(160):
            while True:
                response = await api.get("/api/v1/tasks/" + task_id);assert response.status_code == 200
                task = response.json()
                if task["status"] in TERMINAL:break
                if phase != "cancel-ready" and business is None:
                    record = await sdk.get_workspace(before["workspaceId"])
                    if record.status_code == 200 and record.body["phase"] == "bound":business = observe_pod(record.body)
                await asyncio.sleep(.05)
        expected_status = "cancelled" if phase == "cancel-ready" else "succeeded"
        assert task["status"] == expected_status, task.get("error")
        after = state_for(journal, task_id)
        assert all(after[k] == before[k] for k in ("taskId", "sessionId", "branchId", "workspaceId", "limits", "maxRecoveries"))
        all_states = [r["data"] for r in entries(journal) if r["type"] == "task.state" and r["data"]["taskId"] == task_id]
        verify_phase_clocks(before, after, all_states)
        assert all(a["ownerStopped"] for a in after["attempts"])
        assert all(after["calls"][key] == value for key, value in before["calls"].items())
        assert len(after["attempts"]) == (1 if phase == "cancel-ready" else 2)
        assert state_for(journal, cp["identity"]["taskId"]) == original_task
        cp_rows = entries(cp_path)
        requests = [r["data"] for r in cp_rows if r["type"] == "checkpoint.restore" and r["data"]["restoreId"] == rid]
        assert [r["status"] for r in requests] == (["RESERVED", "FAILED"] if phase == "cancel-ready" else ["RESERVED", "FILES_READY", "ATTACHED"])
        go_rows = entries(root / "go-control/workspaces.jsonl", go_format=True)
        submissions = [r for r in go_rows if r["type"] == "workspace.barrier" and r["data"]["jobId"] == rid and r["data"]["phase"] == "submitted"]
        assert len(submissions) == 1
        if phase == "cancel-ready":
            assert not after["calls"] and after["cancelRequested"]
            assert not any(r for r in go_rows if r["type"] == "workspace.state" and r["data"]["workspaceId"] == after["workspaceId"] and r["data"]["phase"] == "bound")
            final_barrier = (await sdk.get_workspace_restore(after["workspaceId"])).body
            assert final_barrier["phase"] == "released" and not final_barrier.get("restoreVerified", False)
            assert (await sdk.claim_workspace(after["workspaceId"], task_id, new_id("attempt"))).status_code == 409
            summary = await api.get("/api/v1/sessions/" + sid)
            assert summary.json()["activeLeafId"] == original_task["completeNodeId"]
            report["failedTargetQuarantined"] = True
        else:
            assert business and business["imageId"] == cp["identity"]["imageId"]
            assert sum(c["kind"] == "model" for c in after["calls"].values()) == 2
            assert sum(c["kind"] == "tool" for c in after["calls"].values()) == 1
            assert sum(c["operation"] == "write_file" for c in after["calls"].values()) == (1 if phase == "after-tool" else 0)
            if phase == "after-tool":assert business["podUid"] != report["businessBefore"]["podUid"]
            # Agent仍持单写入锁；只读校验过checksum的完整日志，不抢写锁。
            records = {r["data"]["checkpointId"]: r["data"] for r in cp_rows if r["type"] == "checkpoint.state"}
            heads = [r["data"] for r in cp_rows if r["type"] == "checkpoint.state"
                     and r["data"]["status"] == "COMMITTED" and r["data"]["identity"]["branchId"] == after["branchId"]]
            head = heads[-1];assert head["revision"] == 2
            initial = records[head["parentCheckpointId"]];assert initial["parentCheckpointId"] == cp["checkpointId"] and initial["revision"] == 1
            prefix = SessionReferences(cp_path.parent).read(head["identity"]["sessionRef"])
            messages = [n["message"] for n in prefix["nodes"]]
            assert _branchable_flags(messages)[-1]
            calls = Counter(c["id"] for m in messages if m["role"] == "assistant" for c in m.get("toolCalls", []))
            results = Counter(m["toolCallId"] for m in messages if m["role"] == "tool")
            assert calls == results and all(n == 1 for n in calls.values())
            report.update(finalCheckpoint=head, visibleToolCallIds=list(calls))
            actual = checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum",
                              "/export/workspaces/" + after["workspaceId"] + "/report.md"]).split()[0]
            expected_hash = hashlib.sha256(NEW_CONTENT.encode()).hexdigest() if phase == "after-tool" else source["fileSha256"]
            assert actual == expected_hash
            if phase == "after-tool":assert actual != source["fileSha256"]
            report["fileSha256"] = actual
        assert all(hashlib.sha256((original / name).read_bytes()).hexdigest() == digest for name, digest in original_hashes.items())
        report.update(passed=True, after=after, restoreAfter=requests[-1], business=business, restoreSubmissions=1,
                      originalControlUnchanged=True, faultToVerificationMs=(time.perf_counter() - started) * 1000)
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "message": public_error(error)}
        raise
    finally:
        for process, log in reversed(processes):
            if process.poll() is None:
                try:os.kill(process.pid, signal.SIGCONT)
                except ProcessLookupError:pass
            stop_agent(process, log)
        tasks = None;coordinator = None
        try:
            if journal.exists():
                tasks = RecoveryLedger(journal);refs = SessionReferences(cp_path.parent)
                coordinator = CheckpointCoordinator(sdk, CheckpointLedger(cp_path, refs), refs, "repo-component")
                restorer = RestoreCoordinator(coordinator)
                runner = AgentRunner(object(), sdk, ReplayModelGateway(script), checkpoints=coordinator)
                if task_id:
                    state = tasks.get(task_id)
                    if state["attempts"] and not state["attempts"][-1]["ownerStopped"]:
                        recovery = RecoveryAttempt(tasks, task_id, state["attempts"][-1]["attemptId"])
                        request = restorer.for_task(task_id)
                        await restorer.cleanup(request, recovery)
                        await runner.stop_attempt(recovery)
        except Exception as error:
            report["cleanupError"] = public_error(error);report["passed"] = False
        finally:
            if coordinator:coordinator.close()
            if tasks:tasks.close()
        await api.aclose();await sdk.close();stop_go(go, go_log)
        output.parent.mkdir(parents=True, exist_ok=True);output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=PHASES);parser.add_argument("--output", type=Path)
    parser.add_argument("--source-report", type=Path);parser.add_argument("--secret-name");parser.add_argument("--minio-ip")
    parser.add_argument("--child-phase", choices=PHASES);parser.add_argument("--child-root", type=Path)
    args = parser.parse_args()
    if args.child_phase:
        if args.child_root is None:parser.error("child root required")
        install_cutpoint(args.child_root, args.child_phase)
        import uvicorn
        from .app.main import create_app
        uvicorn.run(create_app(), host="127.0.0.1", port=18090, log_level="warning", access_log=False)
        return
    if not all((args.phase, args.output, args.source_report, args.secret_name, args.minio_ip)):parser.error("phase/output/source-report/secret-name/minio-ip required")
    if args.output.exists():parser.error("preserve existing evidence; choose another output")
    report = asyncio.run(run(args.output, args.phase, args.source_report, args.secret_name, args.minio_ip))
    print(json.dumps({"passed": report["passed"], "phase": args.phase, "output": str(args.output)}))
    if not report["passed"]:raise SystemExit(1)


if __name__ == "__main__":main()
