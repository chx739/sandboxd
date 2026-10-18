"""V2-06/07/08缺口的真实服务验收；仅操作本轮新合成仓库。

Agent为真实ASGI处理器/TaskStore/Runner，模型固定Replay；Go使用实际TCP。
只遗忘本轮新建的首份非head快照，不改旧仓库、镜像tag或业务权限。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
from unittest.mock import patch
from uuid import uuid4

import httpx

from .app.config import Settings
from .app.main import create_app
from .app.redaction import public_error
from .app.runtime.checkpoint_coordinator import CheckpointCoordinator
from .app.runtime.checkpoints import CheckpointLedger
from .app.runtime.recovery import new_id
from .app.runtime.session_refs import SessionReferences
from .app.store import TaskStore
from .app.tools.remote_files import WorkspaceBinding
from .checkpoint_cleanup import CleanupClient, CleanupCoordinator
from .workspace_checkpoint_demo import CheckpointObservedClient
from .workspace_checkpoint_fault_demo import observe_maintenance, require_idle_ports
from .workspace_demo import REPO, checked, ready, start_go, stop_go
from .workspace_deployment import DEPLOYMENTS, initialize

CONTENT = "synthetic checkpoint refusal acceptance\n"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_bytes(path, contents):
    temporary = path.parent / (".acceptance-" + uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(contents); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def source_from(checkpoint):
    return {**{k: checkpoint["data"][k] for k in ("snapshotId", "manifestSha256", "repositoryCryptoId")},
            **{k: checkpoint["identity"][k] for k in ("imageRef", "imageId")}}


def source_with_fault(source, kind):
    value = dict(source)
    if kind == "snapshot-missing": value["snapshotId"] = hashlib.sha256(uuid4().bytes).hexdigest()
    elif kind == "manifest-mismatch": value["manifestSha256"] = "0" * 64 if source["manifestSha256"] != "0" * 64 else "1" * 64
    elif kind == "image-id-mismatch": value["imageId"] = "sha256:" + "0" * 64
    else: raise ValueError("unknown fixed refusal case")
    if value == source: raise ValueError("fault did not change source")
    return value


def inventory(root, state):
    values = json.loads((root / "repository-credentials.json").read_text())
    binary = Path.home() / ".local/share/sandboxd-tools/restic-0.18.1/restic"
    assert digest(binary) == "01143daba61a1dc8afb0cb3d03ba83e6b118f55b6b146b27473a09efc3df13d6"
    env = {"PATH": os.environ["PATH"], "HOME": str(Path.home()), "RESTIC_PASSWORD": values["password"],
           "AWS_ACCESS_KEY_ID": values["access-key"], "AWS_SECRET_ACCESS_KEY": values["secret-key"],
           "RESTIC_REPOSITORY": "s3:http://127.0.0.1:9002/sandboxd-workspace/" + state["prefix"], "AWS_DEFAULT_REGION": "us-east-1"}
    process = subprocess.run([str(binary), "--no-cache", "-o", "s3.connections=2", "-o", "s3.bucket-lookup=path", "snapshots", "--json"],
                             env=env, capture_output=True, timeout=60)
    if process.returncode: raise RuntimeError("fixed new repository inventory failed")
    return sorted(row["id"] for row in json.loads(process.stdout))


class ObservedClient(CheckpointObservedClient):
    def __init__(self, token):
        super().__init__(token)
        self.backup_calls = []

    async def workspace_backup(self, action, binding, cp, job, *, timeout=90):
        reply = await super().workspace_backup(action, binding, cp, job, timeout=timeout)
        self.backup_calls.append({"action": action, "checkpointId": cp, "jobId": job, "statusCode": reply.status_code})
        return reply


class RepeatedSaveCoordinator(CheckpointCoordinator):
    """管理夹具在首个真实save返回后重复同一调用，再短暂留出API检查窗口。"""
    def __init__(self, *args, go_journal):
        super().__init__(*args)
        self.go_journal = go_journal
        self.saved, self.release = asyncio.Event(), asyncio.Event()
        self.first = self.repeat = None

    async def save(self, recovery, journal, binding, node_id):
        result = await super().save(recovery, journal, binding, node_id)
        if self.first is None:
            self.first = result
            paths = [self.go_journal, self.ledger._journal.path]
            before = {str(p): digest(p) for p in paths}
            call_count = len(self.client.backup_calls)
            again = await super().save(recovery, journal, binding, node_id)
            assert again == result and all(digest(p) == before[str(p)] for p in paths)
            assert self.client.backup_calls[call_count:] == [{"action": "finish", "checkpointId": result["checkpointId"],
                "jobId": "backup-" + result["checkpointId"][3:], "statusCode": 200}]
            self.repeat = {"checkpointId": result["checkpointId"], "snapshotId": result["data"]["snapshotId"],
                           "revision": result["revision"], "sameResponse": True, "journalsUnchanged": before,
                           "repeatCalls": self.client.backup_calls[call_count:]}
            self.saved.set()
            await asyncio.wait_for(self.release.wait(), 20)
        return result


async def run(output):
    if output.exists(): raise ValueError("preserve previous evidence")
    require_idle_ports((10880,))
    for ns in ("sandboxd-demo", "sandboxd-storage"):
        assert not json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", ns, "get", "pods", "-o", "json"]))["items"]
    root = DEPLOYMENTS / ("acceptance-" + uuid4().hex)
    state = initialize(root)
    operator = secrets.token_urlsafe(32)
    go, log, token = start_go(root, REPO / ".cache/workspace-sandboxd", state["nfsIp"], backup_secret=state["secretName"],
                            backup_minio_ip=state["minioIp"], backup_image=state["backupImage"],
                            backup_owner=state["owner"], operator_token=operator)
    sdk = ObservedClient(token)
    replay = root / "acceptance-replay.json"
    replace_bytes(replay, json.dumps({"kind": "deterministic-policy-case", "responses": [
        {"toolCalls": [{"id": "write-once", "name": "write_file", "args": {"path": "report.md", "content": CONTENT}}]},
        {"content": '{"summary":"Synthetic checkpoint acceptance","recommendation":"verify references"}'}]}).encode())
    api_token = secrets.token_urlsafe(32)
    cfg = Settings(listen_host="127.0.0.1", listen_port=18090, api_token=api_token, alert_token=secrets.token_urlsafe(32),
                   prometheus_url="http://127.0.0.1:19090", sandboxd_url="http://127.0.0.1:10880", sandboxd_token=token,
                   llm_mode="replay", llm_base_url="", llm_model="", llm_api_key="", llm_thinking="default", replay_file=replay,
                   trace_dir=root / "legacy-traces", workspace_backend="nfs", recovery_dir=root / "agent-control",
                   checkpoint_mode="restic", checkpoint_repository_id="repo-acceptance")
    report = {"passed": False, "mode": "actual-v2-save-idempotence-and-restore-refusals", "externalModelCalls": 0,
              "controlRoot": str(root), "owner": state["owner"], "cases": [], "scope": "Actual Agent ASGI/TaskStore/Runner with Replay and Go TCP/gVisor/NFS/restic/MinIO; not Agent TCP, initializer SIGKILL, S3 pack corruption or full V2 completion",
              "binarySha256": digest(REPO / ".cache/workspace-sandboxd")}
    targets = []; restoration = None
    def file_hash(workspace):
        return checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum",
                        "/export/workspaces/" + workspace + "/report.md"]).split()[0]
    def coordinator_factory(*args):
        result = RepeatedSaveCoordinator(*args, go_journal=root / "go-control/workspaces.jsonl")
        captured["coordinator"] = result
        return result
    def store_factory(*args, **kwargs):
        result = TaskStore(*args, **kwargs); captured["store"] = result; return result
    async def require(reply, code=200):
        assert reply.status_code == code, (reply.status_code, reply.body)
        return reply.body
    try:
        await ready(go)
        captured = {}
        # 仅观察/重复真实save，不替换处理器、存储后端或诊断模型执行逻辑。
        with patch("agentd.app.main.SandboxdClient", lambda url, auth: sdk), patch("agentd.app.main.TaskStore", store_factory), \
                patch("agentd.app.main.CheckpointCoordinator", coordinator_factory):
            app = create_app(cfg)
        async with app.router.lifespan_context(app):
            store, coordinator = captured["store"], captured["coordinator"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test",
                                        headers={"Authorization": "Bearer " + api_token}) as api:
                task_reply = await api.post("/api/v1/tasks", json={"summary": "synthetic acceptance", "labels": {"cluster": "local-kind"}})
                assert task_reply.status_code == 202
                task_id = task_reply.json()["taskId"]
                try:
                    await asyncio.wait_for(coordinator.saved.wait(), 60)
                    first = coordinator.first
                    state_before = store._ledger.get(task_id)
                    before = {p: digest(root / p) for p in ("agent-control/tasks.jsonl", "agent-control/checkpoints/checkpoints.jsonl", "go-control/workspaces.jsonl")}
                    route = f"/api/v1/sessions/{first['identity']['sessionId']}/checkpoints/{first['checkpointId']}/restore/{new_id('restore')}"
                    refused = await api.post(route)
                    assert refused.status_code == 409 and "未完成任务" in refused.json()["detail"]
                    target = {k: first["identity"][k] for k in ("workspaceId", "sessionId", "branchId")}
                    direct = await sdk.workspace_restore("reserve", target, first["checkpointId"], "restore-" + uuid4().hex, source_from(first))
                    assert direct.status_code == 409
                    assert all(digest(root / p) == h for p, h in before.items())
                    assert file_hash(target["workspaceId"]) == hashlib.sha256(CONTENT.encode()).hexdigest()
                    report["activeSourceRefusal"] = {"agentStatus": refused.status_code, "detail": refused.json()["detail"],
                        "goStatus": direct.status_code, "source": target, "sourcePodUid": first["identity"]["podUid"],
                        "journalsUnchanged": before, "fileSha256": file_hash(target["workspaceId"])}
                    report["saveRetry"] = coordinator.repeat
                finally:
                    coordinator.release.set()
                async with asyncio.timeout(90):
                    while (await store.get(task_id)).status not in {"succeeded", "failed", "cancelled", "interrupted", "limit_exceeded"}:
                        await asyncio.sleep(.1)
                task = await store.get(task_id); assert task.status == "succeeded", task.error
                final_state = store._ledger.get(task_id)
                head = coordinator.ledger.head(task.session_id, final_state["branchId"])
                assert head["revision"] == 2 and head["parentCheckpointId"] == first["checkpointId"]
                report.update(taskId=task_id, sessionId=task.session_id, sourceCheckpoints=[first, head], sourceTask=final_state,
                              backupMaintenance=sdk.maintenance, businessPods=sdk.observations)
                # 在本轮新生成的不可变引用上注入校验失败；原件先另存并在finally恢复。
                reference = root / "agent-control/checkpoints" / head["identity"]["sessionRef"]["path"]
                original = reference.read_bytes()
                backup = root / "synthetic-reference-original.json"; replace_bytes(backup, original)
                restoration = (reference, original)
                before = digest(root / "agent-control/tasks.jsonl")
                replace_bytes(reference, b'{"syntheticCorruption":true}\n')
                refused = await api.post(f"/api/v1/sessions/{task.session_id}/checkpoints/{head['checkpointId']}/restore/{new_id('restore')}")
                assert refused.status_code == 409 and digest(root / "agent-control/tasks.jsonl") == before
                replace_bytes(reference, original); restoration = None
                assert digest(reference) == head["identity"]["sessionRef"]["sha256"]
                report["sessionReferenceRefusal"] = {"statusCode": 409, "detail": refused.json()["detail"], "originalRestored": True,
                    "reference": str(reference.relative_to(root)), "sha256": digest(reference), "taskJournalUnchanged": before}
        # 上一lifespan已关闭Agent/账本/SDK；管理与负面后端测试使用独立可信客户端。
        sdk = ObservedClient(token)
        original_hashes = {p: digest(root / p) for p in ("agent-control/tasks.jsonl", "agent-control/sessions/" + report["sessionId"] + ".jsonl")}
        original_file = file_hash(head["identity"]["workspaceId"])
        source = source_from(head)
        before_inventory = inventory(root, state)
        assert set(before_inventory) == {state["owner"]["ownerSnapshotId"], first["data"]["snapshotId"], head["data"]["snapshotId"]}
        for kind in ("snapshot-missing", "manifest-mismatch", "image-id-mismatch"):
            selected = source_with_fault(source, kind)
            if kind == "snapshot-missing": assert selected["snapshotId"] not in before_inventory
            target = {"workspaceId": new_id("workspace"), "sessionId": report["sessionId"], "branchId": new_id("branch")}
            job = "restore-" + uuid4().hex; targets.append((target, job, selected))
            await require(await sdk.prepare_workspace(target["workspaceId"], target["sessionId"], target["branchId"]), 201)
            await require(await sdk.workspace_restore("reserve", target, head["checkpointId"], job, selected))
            await require(await sdk.workspace_restore("start", target, head["checkpointId"], job, selected))
            waited = await sdk.workspace_restore("wait", target, head["checkpointId"], job, selected)
            barrier = await require(await sdk.get_workspace_restore(target["workspaceId"]))
            maintenance = observe_maintenance(barrier)
            case = {"kind": kind, "source": selected, "target": target, "jobId": job, "waitStatus": waited.status_code, "maintenance": maintenance}
            if kind != "image-id-mismatch":
                await require(waited, 503)
                await require(await sdk.workspace_restore("finish", target, head["checkpointId"], job, selected), 409)
                await require(await sdk.workspace_restore("abort", target, head["checkpointId"], job, selected))
                await require(await sdk.workspace_restore("fail", target, head["checkpointId"], job, selected))
                await require(await sdk.claim_workspace(target["workspaceId"], "task-negative", "attempt-a"), 409)
                names = checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "ls", "-A", "/export/workspaces/" + target["workspaceId"]])
                assert not names.strip()
                case.update(finishStatus=409, claimStatus=409, targetEmpty=True)
            else:
                await require(waited)
                await require(await sdk.workspace_restore("finish", target, head["checkpointId"], job, selected))
                await require(await sdk.claim_workspace(target["workspaceId"], "task-negative", "attempt-a"), 409)
                record = await require(await sdk.get_workspace(target["workspaceId"]))
                assert record["phase"] != "bound"
                actual = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-demo", "get", "pod", record["owner"]["podName"], "-o", "json"]))
                actual_image = actual["status"]["containerStatuses"][0]["imageID"]
                assert actual_image == source["imageId"] and actual_image != selected["imageId"]
                binding = WorkspaceBinding.from_record(record)
                await require(await sdk.workspace_files(binding.as_request(), {"operation": "read_file", "path": "report.md"}), 409)
                await require(await sdk.release_workspace(binding.as_request()))
                assert file_hash(target["workspaceId"]) == original_file
                case.update(claimStatus=409, fileToolStatus=409, actualImageId=actual_image, expectedImageId=selected["imageId"], fileSha256=original_file, writerStopped=True)
            case["passed"] = True; report["cases"].append(case)
        assert inventory(root, state) == before_inventory
        # 只遗忘本轮新建的首份非head合成快照；Agent已关闭，不给它Operator身份。
        refs = SessionReferences(root / "agent-control/checkpoints")
        ledger = CheckpointLedger(root / "agent-control/checkpoints/checkpoints.jsonl", refs)
        admin = CleanupClient("http://127.0.0.1:10880", operator)
        try:
            preview = ledger.cleanup_preview()
            gc = new_id("gc")
            ledger.maintenance.plan(gc, "forget", first["data"]["snapshotId"], preview["planSha256"])
            completed = await CleanupCoordinator(ledger, admin).run(gc)
            assert completed["status"] == "DONE" and ledger.head(report["sessionId"], head["identity"]["branchId"]) == head
            report["forgotten"] = {"gcId": gc, "checkpointId": first["checkpointId"], "snapshotId": first["data"]["snapshotId"], "headCheckpointId": head["checkpointId"], "status": completed["status"]}
        finally:
            await admin.close(); ledger.close()
        assert set(inventory(root, state)) == {state["owner"]["ownerSnapshotId"], head["data"]["snapshotId"]}
        # 真实处理器重新打开原目录；遗忘的旧节点请求必须明确拒绝且无新Task。
        app = create_app(cfg)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test", headers={"Authorization": "Bearer " + api_token}) as api:
                refused = await api.post(f"/api/v1/sessions/{report['sessionId']}/checkpoints/{first['checkpointId']}/restore/{new_id('restore')}")
                assert refused.status_code == 409
                report["forgotten"]["restoreStatus"] = refused.status_code
                report["forgotten"]["detail"] = refused.json()["detail"]
                history = await api.get(f"/api/v1/sessions/{report['sessionId']}/path/{first['identity']['sessionNodeId']}")
                assert history.status_code == 200
                report["forgotten"]["historicalMessagesStatus"] = history.status_code
        assert all(digest(root / p) == h for p, h in original_hashes.items())
        assert file_hash(head["identity"]["workspaceId"]) == original_file
        report.update(passed=True, originalControlHashes=original_hashes, sourceFileSha256=original_file,
                      inventoryBefore=before_inventory, inventoryAfter=inventory(root, state))
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": public_error(exc)}
        raise
    finally:
        if restoration is not None:
            replace_bytes(*restoration)
        for target, job, selected in reversed(targets):
            try:
                barrier = await sdk.get_workspace_restore(target["workspaceId"])
                if barrier.status_code == 200 and barrier.body["phase"] != "released":
                    await require(await sdk.workspace_restore("abort", target, head["checkpointId"], job, selected))
                    action = "finish" if barrier.body.get("restoreVerified") else "fail"
                    await require(await sdk.workspace_restore(action, target, head["checkpointId"], job, selected))
                record = await sdk.get_workspace(target["workspaceId"])
                if record.status_code == 200 and record.body.get("owner", {}).get("taskId") == "task-negative" and not record.body["owner"].get("stopConfirmed"):
                    await require(await sdk.release_workspace(WorkspaceBinding.from_record(record.body).as_request()))
            except Exception as exc:
                report.update(passed=False, cleanupError=public_error(exc))
        await sdk.close(); stop_go(go, log)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    assert report["passed"]
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output))
    print(json.dumps({"passed": result["passed"], "cases": len(result["cases"]), "externalModelCalls": 0}))
