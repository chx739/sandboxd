"""新干净仓库实际联合GC；只清理本次新建合成快照，保留所有旧数据。"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
from uuid import uuid4

from .app.models import AlertEvent
from .app.model_gateway import ReplayModelGateway
from .app.runner import AgentRunner
from .app.store import TaskStore
from .app.runtime.checkpoints import CheckpointLedger, CheckpointConflict
from .app.runtime.checkpoint_coordinator import CheckpointCoordinator
from .app.runtime.durable import JournalError, JournalCorrupt
from .app.runtime.recovery import new_id
from .app.runtime.session_refs import SessionReferences
from .app.tools.remote_files import WorkspaceBinding
from .checkpoint_cleanup import CleanupClient, CleanupCoordinator
from .workspace_backup_component import KUBE, MINIO, kube_object, private_json
from .workspace_checkpoint_demo import CheckpointObservedClient
from .workspace_checkpoint_fault_demo import observe_maintenance, require_idle_ports
from .workspace_demo import REPO, checked, ready, start_go, stop_go
from .workspace_migrate import require_ok
from .workspace_repository_lock import RepositoryLock
from .app.redaction import public_error

CONTENTS = ["synthetic owned joint version one\n", "synthetic owned joint version two\n", "synthetic owned joint version three\n"]


def native_root(root: Path) -> Path:
    expected = Path.home() / ".local/share/sandboxd/workspace-gc-joint"
    if root.resolve().parent != expected.resolve() or not re.fullmatch(r"[a-f0-9]{32}", root.name):
        raise ValueError("requires this fixture's own private native root")
    return root.resolve()


def delete_terminated(name: str, uid: str):
    pod = json.loads(checked([*KUBE, "-n", "sandboxd-storage", "get", "pod", name, "-o", "json"]))
    assert pod["metadata"]["uid"] == uid and pod["status"]["phase"] in {"Succeeded", "Failed"}
    assert all("terminated" in s["state"] for s in pod["status"]["containerStatuses"])
    body = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid}}
    result = subprocess.run([*KUBE, "delete", "--raw", "/api/v1/namespaces/sandboxd-storage/pods/" + name, "-f", "-"],
                            input=json.dumps(body), capture_output=True, text=True, timeout=20)
    if result.returncode: raise RuntimeError("fixed UID maintenance cleanup failed")


async def bootstrap(secret: str, endpoint: str, controller: str) -> dict:
    # 仅fresh init bootstrap使用直接可信Pod；后续GC全部通过Go屏障/Operator。
    command = [str(REPO / ".cache/workspace-backup-pod-owned"), "-image", "sandboxd-restic:0.18.1-v3",
               "-id", "init-" + uuid4().hex[:16], "-workspace", new_id("workspace"), "-secret", secret,
               "-endpoint", endpoint, "-operation", "init-owned", "-controller", controller]
    spec = json.loads(checked(command)); kube_object("apply", spec["networkPolicy"])
    pod = kube_object("create", spec["pod"]); name, uid = pod["metadata"]["name"], pod["metadata"]["uid"]
    async with asyncio.timeout(130):
        while True:
            pod = json.loads(checked([*KUBE, "-n", "sandboxd-storage", "get", "pod", name, "-o", "json"]))
            assert pod["metadata"]["uid"] == uid
            if pod["status"]["phase"] in {"Succeeded", "Failed"}: break
            await asyncio.sleep(.2)
    evidence = observe_maintenance({"podUid": uid, "podName": name})
    assert pod["status"]["phase"] == "Succeeded" and pod["status"]["containerStatuses"][0]["state"]["terminated"]["exitCode"] == 0
    assert not any("persistentVolumeClaim" in v for v in pod["spec"]["volumes"])
    result = json.loads(checked([*KUBE, "-n", "sandboxd-storage", "logs", name]))
    assert result["ok"] and result["operation"] == "init-owned" and result["owner"]["controllerId"] == controller
    delete_terminated(name, uid)
    return {"owner": result["owner"], "runtime": evidence, "businessVolumeMounted": False}


class ObservedOperator(CleanupClient):
    def __init__(self, token: str, *, cut_root: Path | None = None):
        super().__init__("http://127.0.0.1:10880", token)
        self.rows = []; self.cut_root = cut_root

    async def request(self, action, record, **kwargs):
        response = await super().request(action, record, **kwargs)
        row = {"action": action, "gcId": record["gcId"], "jobId": record["jobId"], "statusCode": response.status_code}
        if action == "wait":
            body = response.body if response.status_code == 200 else (await self.get()).body
            barrier = body["barrier"] if response.status_code == 200 else body
            row["barrier"] = barrier
            row["runtime"] = observe_maintenance(barrier)
            pod = json.loads(checked([*KUBE, "-n", "sandboxd-storage", "get", "pod", barrier["podName"], "-o", "json"]))
            assert not any("persistentVolumeClaim" in v for v in pod["spec"]["volumes"])
            row["exitCode"] = pod["status"]["containerStatuses"][0]["state"]["terminated"]["exitCode"]
        self.rows.append(row)
        if action == "wait" and response.status_code == 200 and self.cut_root is not None:
            private_json(self.cut_root / "admin-cut.json", {"pid": os.getpid(), "gcId": record["gcId"], "row": row})
            os.kill(os.getpid(), signal.SIGSTOP)  # 只暂停本夹具子进程，父进程核验后SIGKILL。
        return response


async def admin_child(root: Path, gc_id: str):
    root = native_root(root)
    ledger = CheckpointLedger(root / "agent-control/checkpoints/checkpoints.jsonl", SessionReferences(root / "agent-control/checkpoints"))
    client = ObservedOperator(os.environ["SANDBOXD_OPERATOR_TOKEN"], cut_root=root)
    try:
        await CleanupCoordinator(ledger, client).run(gc_id)
    finally:
        await client.close(); ledger.close()


async def run(output: Path):
    require_idle_ports((10880,))
    assert checked([*KUBE, "config", "current-context"]).strip() == "kind-sandboxd"
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    server = json.loads(checked(["docker", "inspect", MINIO]))[0]
    assert nfs["State"]["Running"] and not nfs["State"]["Paused"] and server["State"]["Running"] and not server["State"]["Paused"]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace-backup"
    endpoint, nfs_ip = server["NetworkSettings"]["Networks"]["kind"]["IPAddress"], nfs["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    root = Path.home() / ".local/share/sandboxd/workspace-gc-joint" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    credentials = private_json(Path.home() / ".local/share/sandboxd/workspace-backup/minio-credentials.json")
    values = {"access-key": credentials["access"], "secret-key": credentials["secret"], "password": secrets.token_urlsafe(32),
              "repository": "s3:http://" + endpoint + ":9000/sandboxd-workspace/owned-joint/" + root.name}
    secret, controller, operator = "restic-" + uuid4().hex[:16], new_id("controller"), secrets.token_urlsafe(32)
    report = {"passed": False, "mode": "actual-owned-agent-checkpoints-operator-gc-joint", "externalModelCalls": 0,
              "controlRoot": str(root), "secretName": secret, "scope": "Actual Replay Agent CPs and operator retention/cleanup, admin+Go SIGKILL, exclusive lock, pinned file restore; not new historical branch Task resume or performance benchmark.",
              "cases": [], "operatorCalls": [], "cleanup": []}
    go = go_log = sdk = ledger = store = worker = admin = watchdog = admin_log = lock = None
    bindings = []
    try:
        report["stage"] = "bootstrap"
        kube_object("create", {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": secret,
            "namespace": "sandboxd-storage", "labels": {"sandbox.io/managed-by": "sandboxd-backup"}},
            "data": {k: base64.b64encode(v.encode()).decode() for k, v in values.items()}})
        report["bootstrap"] = await bootstrap(secret, endpoint, controller)
        print("fresh owned repository initialized", flush=True)
        owner = report["bootstrap"]["owner"]
        private_json(root / "owner-binding.json", {"owner": owner, "secretName": secret})
        private_json(root / "operator-auth.json", {"operatorToken": operator})

        def launch():
            return start_go(root, REPO / ".cache/workspace-sandboxd-owned-cleanup", nfs_ip, backup_secret=secret,
                            backup_minio_ip=endpoint, backup_image="sandboxd-restic:0.18.1-v3", backup_owner=owner, operator_token=operator)
        go, go_log, token = launch(); await ready(go)
        sdk = CheckpointObservedClient(token)
        refs = SessionReferences(root / "agent-control/checkpoints")
        ledger = CheckpointLedger(root / "agent-control/checkpoints/checkpoints.jsonl", refs)
        ledger.maintenance.bind(owner)
        replay = root / "replay.json"
        responses = []
        for index, content in enumerate(CONTENTS):
            args = {"path": "report.md", "content": content}
            if index: args["expectedSha256"] = hashlib.sha256(CONTENTS[index - 1].encode()).hexdigest()
            responses.append({"toolCalls": [{"id": "version-" + str(index), "name": "write_file", "args": args}]})
        responses += [{"toolCalls": [{"id": "verify-current", "name": "read_file", "args": {"path": "report.md"}}]},
                      {"content": '{"summary":"Synthetic owned versions verified","recommendation":"review evidence"}'}]
        replay.write_text(json.dumps({"kind": "deterministic-policy-case", "model": "owned-joint-replay", "responses": responses})); replay.chmod(0o600)
        store = TaskStore(root / "legacy-traces", recovery_dir=root / "agent-control")
        coordinator = CheckpointCoordinator(sdk, ledger, refs, "repo-owned-joint")
        runner = AgentRunner(object(), sdk, ReplayModelGateway(replay), checkpoints=coordinator)
        task = await store.enqueue(AlertEvent(labels={"cluster": "local-kind", "namespace": "sandboxd-target"}, annotations={"summary": "owned synthetic GC acceptance"}))
        worker = asyncio.create_task(store.worker(runner))
        report["stage"] = "agent-joint-checkpoints"
        async with asyncio.timeout(240):
            while True:
                task = await store.get(task.task_id)
                if task.status in {"succeeded", "failed", "cancelled", "limit_exceeded", "interrupted"}: break
                await asyncio.sleep(.1)
        assert task.status == "succeeded", task.error
        print("Replay Agent completed five joint checkpoints", flush=True)
        state = store._ledger.get(task.task_id)
        head = ledger.head(task.session_id, state["branchId"]); assert head["revision"] == 5
        chain = []; current = head
        while current:
            chain.append(current); current = ledger.get(current["parentCheckpointId"]) if current["parentCheckpointId"] else None
        chain.reverse(); assert len(chain) == 5 and len(sdk.maintenance) == 5
        report.update(checkpoints=chain, taskId=task.task_id, workspaceId=task.workspace_id,
                      backupMaintenance=sdk.maintenance, businessPods=sdk.observations)
        worker.cancel(); await asyncio.gather(worker, return_exceptions=True); worker = None
        store.close(); store = None
        originals = [root / "agent-control/tasks.jsonl", root / "agent-control/sessions" / (task.session_id + ".jsonl")]
        original_hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
        ledger.retain(chain[1]["checkpointId"], True)
        restore = ledger.prepare_restore(new_id("restore"), chain[3]["checkpointId"], session_id=task.session_id,
            branch_id=new_id("branch"), workspace_id=new_id("workspace"), task_id=new_id("task"))
        ledger.advance_restore(restore["restoreId"], "FAILED", "cancelled")  # 管理计划取消仍保留来源；没有伪称执行过恢复。
        denied = []
        for cp in (chain[1], chain[3], chain[4]):
            try: ledger.maintenance.plan(new_id("gc"), "forget", cp["data"]["snapshotId"], ledger.cleanup_preview()["planSha256"])
            except JournalCorrupt: denied.append(cp["checkpointId"])
            else: raise AssertionError("protected snapshot accepted")
        assert len(denied) == 3
        report["cases"].append({"case": "head-pin-retained-restore-source", "passed": True, "refusedCheckpointIds": denied,
                                "restoreSourceStatus": "FAILED(cancelled plan; no remote restore)"})
        first_gc = ledger.maintenance.plan(new_id("gc"), "forget", chain[0]["data"]["snapshotId"], ledger.cleanup_preview()["planSha256"])
        report["stage"] = "admin-sigkill-cut"
        ledger.close(); ledger = None

        admin_log = (root / "admin-child.log").open("a"); (root / "admin-child.log").chmod(0o600)
        admin = subprocess.Popen([sys.executable, "-m", "agentd.workspace_gc_joint_demo", "--admin-child", str(root), "--gc-id", first_gc["gcId"]],
            cwd=REPO, env={"PATH": os.environ["PATH"], "HOME": str(Path.home()), "SANDBOXD_OPERATOR_TOKEN": operator}, stdout=admin_log, stderr=subprocess.STDOUT)
        starttime = Path(f"/proc/{admin.pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
        code = ("import os,pathlib,signal,sys,time;time.sleep(180);p=int(sys.argv[1]);f=pathlib.Path('/proc/'+str(p)+'/stat');"
                "s=f.read_text().rsplit(') ',1)[1].split()[19] if f.exists() else '';os.kill(p,signal.SIGKILL) if s==sys.argv[2] else None")
        watchdog = subprocess.Popen([sys.executable, "-c", code, str(admin.pid), starttime], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        async with asyncio.timeout(140):
            while not (root / "admin-cut.json").exists():
                assert admin.poll() is None, "owned admin child ended before cut; private log retained"
                await asyncio.sleep(.1)
        cut = private_json(root / "admin-cut.json"); assert cut["pid"] == admin.pid and cut["gcId"] == first_gc["gcId"]
        _, stopped = os.waitpid(admin.pid, os.WUNTRACED); assert os.WIFSTOPPED(stopped)
        admin.kill(); assert admin.wait(timeout=5) == -signal.SIGKILL
        watchdog.terminate(); watchdog.wait(timeout=5); watchdog = None
        go.kill(); assert go.wait(timeout=5) == -signal.SIGKILL; go_log.close(); go = go_log = None
        await sdk.close(); sdk = None
        go, go_log, token = launch(); await ready(go); sdk = CheckpointObservedClient(token)
        ledger = CheckpointLedger(root / "agent-control/checkpoints/checkpoints.jsonl", refs)
        assert ledger.forget_request(first_gc["gcId"])["status"] == "PREPARED"
        operator_sdk = ObservedOperator(operator)
        try:
            done = await CleanupCoordinator(ledger, operator_sdk).run(first_gc["gcId"])
            assert done["status"] == "DONE" and done["attempt"] == 1
            assert [r["action"] for r in operator_sdk.rows] == ["wait", "finish"]
            assert operator_sdk.rows[0]["barrier"]["podUid"] == cut["row"]["barrier"]["podUid"]
            report["operatorCalls"] += [cut["row"], *operator_sdk.rows]
        finally: await operator_sdk.close()
        report["cases"].append({"case": "actual-admin-and-go-sigkill-after-verified-wait", "passed": True,
            "gcId": first_gc["gcId"], "adminExitCode": admin.returncode, "goExitCode": -signal.SIGKILL,
            "sameJobId": done["jobId"], "samePodUid": cut["row"]["barrier"]["podUid"], "backendRestarted": False})
        print("admin and Go SIGKILL reconciled without repeating backend job", flush=True)

        second_gc = ledger.maintenance.plan(new_id("gc"), "forget", chain[2]["data"]["snapshotId"], ledger.cleanup_preview()["planSha256"])
        report["stage"] = "exclusive-lock-failure"
        lock_root = Path.home() / ".local/share/sandboxd/workspace-storage-fault" / uuid4().hex
        lock_root.mkdir(mode=0o700, parents=True)
        lock = RepositoryLock(lock_root, secret, endpoint); lock.expected_repository_id = owner["repositoryCryptoId"]; lock.begin()
        lock_ids = list(lock.lock_ids)
        operator_sdk = ObservedOperator(operator)
        try:
            try: await CleanupCoordinator(ledger, operator_sdk).run(second_gc["gcId"])
            except JournalError: pass
            else: raise AssertionError("exclusive lock did not block actual cleanup")
            assert ledger.forget_request(second_gc["gcId"])["status"] == "PREPARED"
            barrier = (await operator_sdk.get()).body
            assert barrier["phase"] == "stopped" and not barrier.get("cleanupResult")
            probe = new_id("workspace"); require_ok(await sdk.prepare_workspace(probe, "session-" + uuid4().hex[:16], new_id("branch")))
            probe_binding = WorkspaceBinding.from_record(require_ok(await sdk.claim_workspace(probe, new_id("task"), new_id("attempt")))).as_request(); bindings.append(probe_binding)
            probe_cp, probe_job = new_id("cp"), "probe-" + uuid4().hex[:16]
            blocked = await sdk.workspace_backup("reserve", probe_binding, probe_cp, probe_job); assert blocked.status_code == 409
            lock.end(); lock = None
            completed = await CleanupCoordinator(ledger, operator_sdk).retry(second_gc["gcId"])
            assert completed["status"] == "DONE" and completed["attempt"] == 2
            assert completed["previousJobId"] == second_gc["jobId"] and completed["jobId"] != second_gc["jobId"]
            report["operatorCalls"] += operator_sdk.rows
            require_ok(await sdk.workspace_backup("reserve", probe_binding, probe_cp, probe_job))
            require_ok(await sdk.workspace_backup("abort", probe_binding, probe_cp, probe_job)); require_ok(await sdk.workspace_backup("finish", probe_binding, probe_cp, probe_job))
            require_ok(await sdk.release_workspace(probe_binding)); bindings.remove(probe_binding)
        finally: await operator_sdk.close()
        report["cases"].append({"case": "actual-exclusive-lock-failure-fixed-target-retry", "passed": True, "gcId": second_gc["gcId"],
            "lockIds": lock_ids, "attempt": completed["attempt"], "blockedBackupStatus": blocked.status_code,
            "postReconcileBackupReserveStatus": 200, "forceUnlockCalls": 0})
        print("exclusive lock failure and explicit retry verified", flush=True)

        prune = ledger.maintenance.plan(new_id("gc"), "prune", "", ledger.cleanup_preview()["planSha256"])
        report["stage"] = "prune-and-retained-file-restore"
        operator_sdk = ObservedOperator(operator)
        try:
            assert (await CleanupCoordinator(ledger, operator_sdk).run(prune["gcId"]))["status"] == "DONE"
            report["operatorCalls"] += operator_sdk.rows
        finally: await operator_sdk.close()
        pinned, prefix = ledger.restore_point(chain[1]["checkpointId"])
        target = {"workspaceId": new_id("workspace"), "sessionId": task.session_id, "branchId": new_id("branch")}
        require_ok(await sdk.prepare_workspace(**{"workspace_id": target["workspaceId"], "session_id": target["sessionId"], "branch_id": target["branchId"]}))
        source = {"snapshotId": pinned["data"]["snapshotId"], "manifestSha256": pinned["data"]["manifestSha256"],
                  "repositoryCryptoId": pinned["data"]["repositoryCryptoId"], "imageRef": pinned["identity"]["imageRef"], "imageId": pinned["identity"]["imageId"]}
        restore_job = "restore-" + uuid4().hex
        for action in ("reserve", "start", "wait"):
            response = require_ok(await sdk.workspace_restore(action, target, pinned["checkpointId"], restore_job, source))
        report["restoredRuntime"] = observe_maintenance(response["barrier"])
        require_ok(await sdk.workspace_restore("finish", target, pinned["checkpointId"], restore_job, source))
        binding = WorkspaceBinding.from_record(require_ok(await sdk.claim_workspace(target["workspaceId"], new_id("task"), new_id("attempt")))).as_request(); bindings.append(binding)
        actual = require_ok(await sdk.workspace_files(binding, {"operation": "read_file", "path": "report.md"}))
        assert actual["content"] == CONTENTS[1] and actual["sha256"] == hashlib.sha256(CONTENTS[1].encode()).hexdigest()
        require_ok(await sdk.release_workspace(binding)); bindings.remove(binding)
        for index in (0, 2):
            assert ledger.get(chain[index]["checkpointId"])["status"] == "COMMITTED" and ledger.file_availability(chain[index]["checkpointId"]) == "forgotten"
            try: ledger.restore_point(chain[index]["checkpointId"])
            except CheckpointConflict: pass
            else: raise AssertionError("forgotten snapshot still advertised as restorable")
        assert ledger.head(task.session_id, state["branchId"])["checkpointId"] == head["checkpointId"]
        assert original_hashes == {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
        live_hash = checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum", "/export/workspaces/" + task.workspace_id + "/report.md"]).split()[0]
        assert live_hash == hashlib.sha256(CONTENTS[2].encode()).hexdigest()
        report["cases"].append({"case": "actual-prune-retained-pinned-files-restore", "passed": True,
            "sourceCheckpointId": pinned["checkpointId"], "target": target, "restoredSha256": actual["sha256"],
            "sessionPrefixVerifiedNodes": len(prefix["nodes"]), "newHistoricalTaskResume": False})
        report.update(passed=True, owner=owner, originalHashes=original_hashes, originalTaskAndSessionUnchanged=True,
                      liveWorkspaceSha256=live_hash, headCheckpointId=head["checkpointId"],
                      forgottenCheckpointIds=[chain[i]["checkpointId"] for i in (0, 2)],
                      modelReservations=sum(c["kind"] == "model" for c in state["calls"].values()),
                      toolReservations=sum(c["kind"] == "tool" for c in state["calls"].values()))
        report["stage"] = "complete"
        print("actual prune and retained pinned version restored", flush=True)
    except BaseException as error:
        report["error"] = public_error(error)
        raise
    finally:
        if lock is not None:
            try: lock.end()
            except Exception as error: report["cleanup"].append({"resource": "own-repository-check-lock", "error": public_error(error)})
        for process in (admin, watchdog):
            if process is not None and process.poll() is None: process.kill(); process.wait(timeout=5)
        if admin_log is not None: admin_log.close()
        if worker is not None: worker.cancel(); await asyncio.gather(worker, return_exceptions=True)
        if store is not None: store.close()
        if ledger is not None: ledger.close()
        if sdk is not None:
            for binding in bindings:
                try: require_ok(await sdk.release_workspace(binding))
                except Exception as error: report["cleanup"].append({"workspaceId": binding["workspaceId"], "error": public_error(error)})
            await sdk.close()
        if go is not None: stop_go(go, go_log)
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path)
    parser.add_argument("--admin-child", type=Path); parser.add_argument("--gc-id")
    args = parser.parse_args()
    if args.admin_child:
        asyncio.run(admin_child(args.admin_child, args.gc_id))
    else:
        if args.output is None or args.output.exists(): raise ValueError("choose a new evidence output path")
        asyncio.run(run(args.output))
