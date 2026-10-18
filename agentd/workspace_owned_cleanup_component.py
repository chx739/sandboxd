"""新专用合成仓库的实际owned helper组件；非Session/Go管理屏障端到端验收。"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
from uuid import uuid4

from .app.redaction import public_error
from .app.runtime.recovery import new_id
from .app.tools.remote_files import WorkspaceBinding
from .workspace_backup_component import KUBE, MINIO, kube_object, private_json
from .workspace_checkpoint_fault_demo import require_idle_ports
from .workspace_demo import REPO, ObservedClient, checked, ready, start_go, stop_go
from .workspace_migrate import require_ok


async def run(output: Path):
    require_idle_ports((10880,))
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    server = json.loads(checked(["docker", "inspect", MINIO]))[0]
    assert nfs["State"]["Running"] and server["State"]["Running"] and not server["State"]["Paused"]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace-backup"
    endpoint = server["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    root = Path.home() / ".local/share/sandboxd/workspace-owned-cleanup" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    credentials = private_json(Path.home() / ".local/share/sandboxd/workspace-backup/minio-credentials.json")
    repository = "s3:http://" + endpoint + ":9000/sandboxd-workspace/owned-cleanup/" + root.name
    secret_name, controller = "restic-" + uuid4().hex[:16], new_id("controller")
    values = {"access-key": credentials["access"], "secret-key": credentials["secret"],
              "password": secrets.token_urlsafe(32), "repository": repository}
    kube_object("create", {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": secret_name, "namespace": "sandboxd-storage", "labels": {"sandbox.io/managed-by": "sandboxd-backup"}},
        "data": {k: base64.b64encode(v.encode()).decode() for k, v in values.items()}})
    process, log, token = start_go(root, REPO / ".cache/workspace-sandboxd-restore-cleanup",
                                   nfs["NetworkSettings"]["Networks"]["kind"]["IPAddress"])
    client = ObservedClient(token)
    owned, active = {}, []
    report = {"mode": "actual-owned-restic-helper-gvisor-minio-component", "passed": False,
              "externalModelCalls": 0, "controlRoot": str(root), "secretName": secret_name,
              "scope": "New synthetic repository only; direct trusted admin Pods and manual quiescence. No Go cleanup barrier/Operator HTTP/Session retention coordinator acceptance.",
              "operations": [], "cleanup": []}

    async def prepare():
        workspace = new_id("workspace")
        require_ok(await client.prepare_workspace(workspace, "session-" + uuid4().hex[:16], new_id("branch")))
        owned[workspace] = new_id("task")
        return workspace

    async def claim(workspace):
        return WorkspaceBinding.from_record(require_ok(await client.claim_workspace(workspace, owned[workspace], new_id("attempt")))).as_request()

    async def release(workspace):
        metadata = require_ok(await client.get_workspace(workspace))
        if metadata.get("owner", {}).get("attemptId"):
            assert metadata["owner"]["taskId"] == owned[workspace]
            stopped = require_ok(await client.release_workspace(WorkspaceBinding.from_record(metadata).as_request()))
            assert stopped["owner"]["stopConfirmed"]

    def delete_pod(name, uid):
        # 明确UID条件；只删除本进程已观察到终止的那个维护实例。
        body = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid}}
        response = subprocess.run([*KUBE, "delete", "--raw", "/api/v1/namespaces/sandboxd-storage/pods/" + name, "-f", "-"],
            input=json.dumps(body), capture_output=True, text=True, timeout=20)
        if response.returncode:
            raise RuntimeError("UID-conditioned maintenance deletion failed; retain for inspection")

    async def operation(workspace, action, *, owner=None, checkpoint=None, snapshot=None, manifest=None, expect_failure=False):
        args = [str(REPO / ".cache/workspace-backup-pod-owned"), "-image", "sandboxd-restic:0.18.1-v3",
                "-id", "owned-" + uuid4().hex[:16], "-workspace", workspace, "-secret", secret_name,
                "-endpoint", endpoint, "-operation", action]
        for key, value in (("-checkpoint", checkpoint), ("-snapshot", snapshot), ("-manifest", manifest),
                           ("-controller", (owner or {}).get("controllerId")),
                           ("-owner-snapshot", (owner or {}).get("ownerSnapshotId")),
                           ("-repository-crypto", (owner or {}).get("repositoryCryptoId"))):
            if value:
                args += [key, value]
        spec = json.loads(checked(args)); kube_object("apply", spec["networkPolicy"])
        pod = kube_object("create", spec["pod"])
        name, uid = pod["metadata"]["name"], pod["metadata"]["uid"]
        active.append((name, uid))
        async with asyncio.timeout(130):
            while True:
                pod = json.loads(checked([*KUBE, "-n", "sandboxd-storage", "get", "pod", name, "-o", "json"]))
                assert pod["metadata"]["uid"] == uid
                if pod["status"]["phase"] in {"Succeeded", "Failed"}:
                    break
                await asyncio.sleep(.2)
        status = pod["status"]["containerStatuses"][0]
        assert "terminated" in status["state"]
        # 原始CRI环境有解析后的Secret，绝不保存或显示。
        cri = json.loads(checked(["docker", "exec", "sandboxd-control-plane", "crictl", "inspect", "-o", "json", status["containerID"].split("://", 1)[1]]))
        runtime = cri["info"]["runtimeType"]
        assert runtime == "io.containerd.runsc.v1" and pod["spec"]["securityContext"]["runAsUser"] == 65532
        mounted = any(v.get("persistentVolumeClaim") for v in pod["spec"]["volumes"])
        if action in {"init-owned", "forget", "prune"}:
            assert not mounted
        result = json.loads(checked([*KUBE, "-n", "sandboxd-storage", "logs", name]))
        code = status["state"]["terminated"]["exitCode"]
        row = {"operation": action, "podUid": uid, "podName": name, "runtimeType": runtime,
               "imageId": status["imageID"], "workspaceMounted": mounted, "exitCode": code,
               "expectedFailure": expect_failure, "result": result}
        report["operations"].append(row)
        if expect_failure:
            assert code != 0 and result["ok"] is False
        else:
            assert code == 0 and result["ok"] is True
        delete_pod(name, uid); active.remove((name, uid))
        print(json.dumps({"operation": action, "expectedFailure": expect_failure, "passed": True}), flush=True)
        return result

    try:
        await ready(process)
        source = await prepare()
        binding = await claim(source)
        one, two = "synthetic owned version one\n", "synthetic owned version two\n"
        require_ok(await client.workspace_files(binding, {"operation": "write_file", "path": "report.md", "content": one}))
        await release(source)
        initialized = await operation(source, "init-owned", owner={"controllerId": controller})
        owner = initialized["owner"]
        private_json(root / "owner-binding.json", {"owner": owner, "secretName": secret_name,
                                                   "scope": "synthetic component fixture only; not deployment adoption"})
        first = await operation(source, "backup", owner=owner, checkpoint=new_id("cp"))
        binding = await claim(source)
        require_ok(await client.workspace_files(binding, {"operation": "write_file", "path": "report.md", "content": two,
                                                         "expectedSha256": hashlib.sha256(one.encode()).hexdigest()}))
        await release(source)
        second = await operation(source, "backup", owner=owner, checkpoint=new_id("cp"))
        await operation(source, "forget", owner=owner, snapshot=owner["ownerSnapshotId"], expect_failure=True)
        await operation(source, "forget", owner=dict(owner, repositoryCryptoId="f" * 64), snapshot=first["snapshotId"], expect_failure=True)
        await operation(source, "forget", owner=dict(owner, controllerId=new_id("controller")), snapshot=first["snapshotId"], expect_failure=True)
        before = await operation(source, "snapshots")
        assert {s["id"] for s in before["result"]} == {owner["ownerSnapshotId"], first["snapshotId"], second["snapshotId"]}
        forgotten = await operation(source, "forget", owner=owner, snapshot=first["snapshotId"])
        assert forgotten["forgotten"] and not forgotten["alreadyAbsent"]
        repeated = await operation(source, "forget", owner=owner, snapshot=first["snapshotId"])
        assert repeated["alreadyAbsent"]
        await operation(source, "prune", owner=owner)
        await operation(source, "check")
        after = await operation(source, "snapshots")
        assert {s["id"] for s in after["result"]} == {owner["ownerSnapshotId"], second["snapshotId"]}
        target = await prepare()
        restored = await operation(target, "restore", snapshot=second["snapshotId"], manifest=second["manifest"]["sha256"])
        assert restored["repository"]["repositoryCryptoId"] == owner["repositoryCryptoId"]
        binding = await claim(target)
        actual = require_ok(await client.workspace_files(binding, {"operation": "read_file", "path": "report.md"}))
        assert actual["content"] == two and actual["sha256"] == hashlib.sha256(two.encode()).hexdigest()
        await release(target)
        # 最后在本新仓库故意生成一个未归属的合成版本，验证拒绝而不自动收编。
        foreign = await operation(source, "backup", checkpoint=new_id("cp"))
        await operation(source, "prune", owner=owner, expect_failure=True)
        retained = await operation(source, "snapshots")
        assert {s["id"] for s in retained["result"]} == {owner["ownerSnapshotId"], second["snapshotId"], foreign["snapshotId"]}
        report.update(passed=True, owner=owner, removedSyntheticSnapshotId=first["snapshotId"],
                      retainedSnapshotId=second["snapshotId"], foreignSyntheticSnapshotId=foreign["snapshotId"],
                      remainingFileSha256=actual["sha256"], businessPods=client.observations,
                      normalForgetPruneAndRestore=True, markerAndForeignOwnershipProtected=True,
                      repositoryPurpose="new isolated synthetic prefix; intentionally foreign-tagged final snapshot; do not reuse as deployment")
    except BaseException as exc:
        report["error"] = public_error(exc)
        raise
    finally:
        for workspace in owned:
            try:
                await release(workspace)
            except Exception as exc:
                report["cleanup"].append({"workspaceId": workspace, "error": public_error(exc)})
        report["retainedMaintenancePods"] = [{"name": name, "uid": uid} for name, uid in active]
        await client.close(); stop_go(process, log)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve existing evidence; choose a new output")
    asyncio.run(run(args.output))
