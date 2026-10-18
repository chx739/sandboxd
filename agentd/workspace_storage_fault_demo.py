"""真实专用MinIO不可用；保留成功检查点，验证下一次联合保存有界失败。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from .app.model_gateway import ReplayModelGateway
from .app.models import AlertEvent
from .app.redaction import public_error
from .app.runner import AgentRunner
from .app.runtime.checkpoint_coordinator import CheckpointCoordinator
from .app.runtime.checkpoints import CheckpointLedger, CheckpointConflict
from .app.runtime.session_refs import SessionReferences
from .app.store import TaskStore
from .workspace_checkpoint_demo import CheckpointObservedClient
from .workspace_checkpoint_fault_demo import require_idle_ports
from .workspace_demo import REPO, checked, ready, start_go, stop_go
from .workspace_repository_lock import RepositoryLock
from .workspace_partial_backup import PartialBackup, READABLE

MINIO = "sandboxd-workspace-backup-minio-1"


class MinioOutage:
    """只暂停当前固定项目容器；独立看门狗在父进程崩溃后仍可恢复服务。"""
    def __init__(self):
        self.container_id = None
        self.watchdog = None

    def begin(self):
        info = json.loads(checked(["docker", "inspect", MINIO]))[0]
        if (info["Name"] != "/" + MINIO
                or info["Config"]["Labels"].get("com.docker.compose.project") != "sandboxd-workspace-backup"
                or not info["State"]["Running"] or info["State"]["Paused"]):
            raise ValueError("requires idle running project-specific MinIO; never alter another container")
        self.container_id = info["Id"]
        # 不经过Shell、不访问凭据；固定150秒是故障夹具保险，不改变Task截止时间。
        code = ("import json,subprocess,sys,time;time.sleep(150);"
                "r=subprocess.run(['docker','inspect',sys.argv[1]],capture_output=True,text=True,timeout=10);"
                "v=json.loads(r.stdout)[0] if r.returncode==0 else None;"
                "subprocess.run(['docker','unpause',sys.argv[1]],stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL,timeout=15) if v and v['State']['Paused'] else None")
        self.watchdog = subprocess.Popen([sys.executable, "-c", code, self.container_id],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
        checked(["docker", "pause", self.container_id])
        assert json.loads(checked(["docker", "inspect", self.container_id]))[0]["State"]["Paused"]

    def end(self):
        if self.container_id:
            info = json.loads(checked(["docker", "inspect", self.container_id]))[0]
            if info["State"]["Paused"]:
                checked(["docker", "unpause", self.container_id])
            state = json.loads(checked(["docker", "inspect", self.container_id]))[0]["State"]
            assert state["Running"] and not state["Paused"]
        # 仅确认恢复成功后关闭看门狗，恢复异常则保留其最后一次保险。
        if self.watchdog:
            self.watchdog.terminate()
            self.watchdog.wait(timeout=5)


class OutageClient(CheckpointObservedClient):
    def __init__(self, token, ledger, outage):
        super().__init__(token)
        self.ledger, self.outage = ledger, outage
        self.failed_checkpoint = None
        self.previous_head = None
        self.events = []
        self.outage_started = None

    async def workspace_backup(self, action, binding, cp, job, *, timeout=90):
        if action == "start" and self.failed_checkpoint is None:
            # WorkspaceBinding没有Session字段，从当前已持久化PREPARING身份取精确作用域。
            record = self.ledger.get(cp)
            identity = record["identity"]
            head = self.ledger.head(identity["sessionId"], identity["branchId"])
            if head is not None:
                assert head["status"] == "COMMITTED" and head["checkpointId"] != cp
                self.previous_head = head
                self.failed_checkpoint = cp
                if isinstance(self.outage, RepositoryLock):
                    self.outage.expected_repository_id = head["data"]["repositoryCryptoId"]
                elif isinstance(self.outage, PartialBackup):
                    self.outage.expected_repository_id = head["data"]["repositoryCryptoId"]
                    self.outage.previous_snapshot_id = head["data"]["snapshotId"]
                    self.outage.workspace_id, self.outage.checkpoint_id = binding["workspaceId"], cp
                    self.outage.business_pod_uid = head["identity"]["podUid"]
                self.outage.begin()
                self.outage_started = time.perf_counter()
                print(json.dumps({"stage": "storage-fault-after-first-commit", "checkpointId": cp,
                                  "faultType": type(self.outage).__name__}), flush=True)
        started = time.perf_counter()
        try:
            response = await super().workspace_backup(action, binding, cp, job, timeout=timeout)
            self.events.append({"action": action, "checkpointId": cp, "httpStatus": response.status_code,
                                "timeoutSeconds": timeout, "elapsedMs": (time.perf_counter() - started) * 1000})
            if action == "start" and response.status_code == 200 and cp == self.failed_checkpoint and isinstance(self.outage, PartialBackup):
                await self.outage.after_start(response.body["barrier"])
            if action == "wait" and response.status_code >= 400 and isinstance(self.outage, (RepositoryLock, PartialBackup)):
                # 只读取本请求固定Pod的结构化helper输出，不保存原始CRI或后端stderr。
                barrier = (await self.get_workspace_backup(binding["workspaceId"])).body
                assert barrier["checkpointId"] == cp and barrier["access"] == binding
                pod = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-storage",
                                          "get", "pod", barrier["podName"], "-o", "json"]))
                assert pod["metadata"]["uid"] == barrier["podUid"]
                status = pod["status"]["containerStatuses"][0]
                cri = json.loads(checked(["docker", "exec", "sandboxd-control-plane", "crictl", "inspect", "-o", "json",
                                          status["containerID"].split("://", 1)[1]]))
                raw = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-storage", "logs", barrier["podName"]]))
                expected_exit = 3 if isinstance(self.outage, PartialBackup) else 11
                assert f"exit={expected_exit}," in raw.get("error", ""), "must be actual expected restic failure"
                assert cri["info"]["runtimeType"] == "io.containerd.runsc.v1"
                self.failed_maintenance = {"podUid": barrier["podUid"], "podName": barrier["podName"],
                    "checkpointId": cp, "runtimeType": cri["info"]["runtimeType"], "imageId": status["imageID"],
                    "helperExitCode": status["state"]["terminated"]["exitCode"], "resticExitCode": expected_exit}
                if isinstance(self.outage, PartialBackup):
                    assert self.outage.pod_read_denied and self.outage.mode() == "0"
                    self.outage.mode_at_failure = "000"
                    # 仍有业务NFS挂载时先恢复原权限，随后才让普通清理释放Pod。
                    self.outage.restore_mode()
            return response
        except BaseException as exc:
            self.events.append({"action": action, "checkpointId": cp, "exceptionType": type(exc).__name__,
                                "timeoutSeconds": timeout, "elapsedMs": (time.perf_counter() - started) * 1000})
            raise


async def run(output: Path, secret: str, endpoint: str, fault: str = "minio"):
    if fault not in {"minio", "repository-lock", "partial-file"}:
        raise ValueError("unknown fixed storage fault")
    require_idle_ports((10880,))
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert nfs["State"]["Running"] and not nfs["State"]["Paused"]
    root = Path.home() / ".local/share/sandboxd/workspace-storage-fault" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    script = root / "replay.json"
    content = "synthetic storage outage report; retained on NFS\n"
    initial_tools = [{"id": "write-report", "name": "write_file", "args": {"path": "report.md", "content": content}}]
    if fault == "partial-file":
        initial_tools.append({"id": "write-readable", "name": "write_file", "args": {"path": "evidence.txt", "content": READABLE}})
    script.write_text(json.dumps({"kind": "deterministic-policy-case", "model": "storage-fault-replay", "responses": [
        {"toolCalls": initial_tools},
        {"toolCalls": [{"id": "read-report", "name": "read_file", "args": {"path": "report.md"}}]},
        {"content": '{"summary":"must not run after failed checkpoint","severity":"warning"}'}]}))
    script.chmod(0o600)
    process, log, token = start_go(root, REPO / ".cache/workspace-sandboxd-restore-cleanup",
        nfs["NetworkSettings"]["Networks"]["kind"]["IPAddress"], backup_secret=secret,
        backup_minio_ip=endpoint, backup_image="sandboxd-restic:0.18.1-v2")
    store = TaskStore(root / "legacy-traces", recovery_dir=root / "agent-control")
    refs = SessionReferences(root / "agent-control/checkpoints")
    ledger = CheckpointLedger(root / "agent-control/checkpoints/checkpoints.jsonl", refs)
    outage = (MinioOutage() if fault == "minio" else RepositoryLock(root, secret, endpoint)
              if fault == "repository-lock" else PartialBackup(root, secret, endpoint, MinioOutage(), content))
    client = OutageClient(token, ledger, outage)
    coordinator = CheckpointCoordinator(client, ledger, refs, "repo-component")
    worker = None
    report = {"mode": "real-agent-replay-go-gvisor-nfs-storage-fault", "fault": fault, "passed": False,
              "scope": "one selected actual storage fault; admin fixture modifies only its own synthetic file for partial-file mode",
              "externalModelCalls": 0, "controlRoot": str(root)}
    try:
        await ready(process)
        runner = AgentRunner(object(), client, ReplayModelGateway(script), checkpoints=coordinator)
        task = await store.enqueue(AlertEvent(labels={"cluster": "local-kind", "namespace": "sandboxd-target"},
                                             annotations={"summary": "synthetic storage outage"}))
        report["taskId"] = task.task_id
        worker = asyncio.create_task(store.worker(runner))
        async with asyncio.timeout(230):
            while True:
                task = await store.get(task.task_id)
                if task.status in {"succeeded", "failed", "interrupted", "cancelled", "limit_exceeded"}:
                    break
                await asyncio.sleep(.2)
        assert client.previous_head and client.failed_checkpoint
        state = store._ledger.get(task.task_id)
        old = client.previous_head
        report.update(taskStatus=task.status, taskError=task.error, workspaceId=task.workspace_id,
                      calls=state["calls"], attempts=state["attempts"], preservedCheckpoint=old,
                      failedCheckpoint=ledger.get(client.failed_checkpoint))
        assert task.status != "succeeded", "failed storage must not claim task success"
        assert ledger.head(task.session_id, state["branchId"]) == old
        assert ledger.get(client.failed_checkpoint)["status"] == "FAILED"
        try:
            ledger.restore_point(client.failed_checkpoint)
        except CheckpointConflict:
            pass
        else:
            raise AssertionError("failed save must not expose a restore point")
        assert not ledger.pending() and state["attempts"][-1]["ownerStopped"]
        assert len(state["calls"]) == (5 if fault == "partial-file" else 4), "no additional model/write retries"
        assert len(state["attempts"]) == 1, "storage failure must not reset task/retry writes"
        elapsed = (time.perf_counter() - client.outage_started) * 1000
        assert elapsed < 145000, "default wait and cleanup must be bounded before watchdog recovery"
        # 故障仍存在时先核对失败结果，防止看门狗恢复服务造成假阳性。
        if fault == "minio":
            assert json.loads(checked(["docker", "inspect", outage.container_id]))[0]["State"]["Paused"]
        elif fault == "repository-lock":
            outage.assert_active()
            report["lockProof"] = {"exclusiveCheckPid": outage.process.pid, "lockIds": outage.lock_ids,
                                   "processStoppedAndLiveAtAssertions": True, "resticBinarySha256": hashlib.sha256((REPO / ".cache/restic-image-v2/restic").read_bytes()).hexdigest()}
            report["failedMaintenance"] = client.failed_maintenance
        else:
            report["partialProof"] = outage.verify_partial()
            report["failedMaintenance"] = client.failed_maintenance
            outage.restore_mode()
        record, prefix = ledger.restore_point(old["checkpointId"])
        assert record == old and prefix["nodes"]
        actual = checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum",
                          "/export/workspaces/" + task.workspace_id + "/report.md"]).split()[0]
        assert actual == hashlib.sha256(content.encode()).hexdigest()
        report.update(passed=True, taskStatus=task.status, taskError=task.error, workspaceId=task.workspace_id,
                      preservedCheckpoint=old, failedCheckpoint=ledger.get(client.failed_checkpoint),
                      outageToTerminalMs=elapsed, calls=state["calls"], attempts=state["attempts"],
                      deadline=state["deadline"], timing=state["timing"], fileSha256=actual,
                      minioStillPausedAtAssertions=fault == "minio", businessPods=client.observations,
                      successfulMaintenance=client.maintenance)
    except BaseException as exc:
        report["error"] = public_error(exc)
        raise
    finally:
        try:
            outage.end()
            report[{"minio": "minioRestored", "repository-lock": "exclusiveLockReleasedNormally",
                    "partial-file": "partialFileModeRestored"}[fault]] = True
        except Exception as exc:
            report["passed"] = False
            report["faultCleanupError"] = public_error(exc)
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        report["events"] = client.events
        if isinstance(outage, PartialBackup):
            report["partialFaultObservation"] = {"snapshotSubcommandObserved": outage.observed_snapshots_before_chmod,
                                                "podReadDenied": outage.pod_read_denied,
                                                "modeAtResticFailure": outage.mode_at_failure}
        coordinator.close()
        store.close()
        await client.close()
        stop_go(process, log)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--secret-name", required=True)
    parser.add_argument("--minio-ip", required=True)
    parser.add_argument("--fault", choices=("minio", "repository-lock", "partial-file"), default="minio")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing evidence; choose a new report path")
    asyncio.run(run(args.output, args.secret_name, args.minio_ip, args.fault))
