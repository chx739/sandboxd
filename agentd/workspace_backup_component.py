"""M4真实后端组件探测；尚非Agent联合检查点验收，不使用真实用户数据。"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from .app.redaction import public_error
from .app.runtime.recovery import new_id
from .app.tools.remote_files import WorkspaceBinding
from .workspace_demo import REPO, ObservedClient, checked, ready, start_go, stop_go
from .workspace_migrate import require_ok

KUBE = ["kubectl", "--context", "kind-sandboxd"]
MINIO = "sandboxd-workspace-backup-minio-1"


def private_json(path: Path, value=None):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        if value is None:
            raise ValueError("private configuration missing")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("configuration requires private native Linux permissions")
    return json.loads(path.read_text())


def kube_object(verb, body):
    # stdout may contain Secret data; never return/log it to the public report.
    response = subprocess.run([*KUBE, verb, "-f", "-", "-o", "json"], input=json.dumps(body),
                              capture_output=True, text=True, timeout=35)
    if response.returncode:
        detail = "" if body.get("kind") == "Secret" else ": " + public_error(response.stderr, 1200)
        raise RuntimeError("trusted Kubernetes object operation failed" + detail)
    return json.loads(response.stdout)


async def minio_ready():
    async with httpx.AsyncClient() as client:
        async with asyncio.timeout(60):
            while True:
                try:
                    if (await client.get("http://127.0.0.1:9002/minio/health/live", timeout=1)).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.2)


async def run(output: Path):
    assert checked([*KUBE, "config", "current-context"]).strip() == "kind-sandboxd"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 10880))
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert nfs["State"]["Running"] and not nfs["State"]["Paused"]
    nfs_server = nfs["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    root = Path.home() / ".local/share/sandboxd/workspace-backup/components" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    shared = Path.home() / ".local/share/sandboxd/workspace-backup"
    credentials = private_json(shared / "minio-credentials.json", {"access": "ws" + secrets.token_hex(12), "secret": secrets.token_urlsafe(36)})
    env_file = shared / "minio.env"
    expected_env = "MINIO_ROOT_USER=" + credentials["access"] + "\nMINIO_ROOT_PASSWORD=" + credentials["secret"] + "\n"
    if not env_file.exists():
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(expected_env)
            f.flush(); os.fsync(f.fileno())
    assert env_file.lstat().st_mode & 0o077 == 0 and not env_file.is_symlink()
    assert env_file.read_text() == expected_env, "private MinIO configuration differs; do not silently rotate credentials"
    env = dict(os.environ, SANDBOXD_BACKUP_MINIO_ENV_FILE=str(env_file))
    subprocess.run(["docker", "compose", "-f", "deploy/workspace/backup-compose.yml", "up", "-d", "--no-recreate", "minio"],
                   cwd=REPO, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    await minio_ready()
    server = json.loads(checked(["docker", "inspect", MINIO]))[0]
    assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace-backup"
    endpoint = server["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    repository = "s3:http://" + endpoint + ":9000/sandboxd-workspace/components/" + root.name
    password = secrets.token_urlsafe(32)
    private_json(root / "repository-credentials.json", dict(credentials, password=password, repository=repository))
    secret_name = "restic-" + uuid4().hex[:16]
    process, log, token = start_go(root, REPO / ".cache/workspace-sandboxd", nfs_server)
    client = ObservedClient(token)
    report = {"mode": "real-go-gvisor-nfs-restic-minio-component", "passed": False, "externalModelCalls": 0,
              "scope": "trusted backend component only; no Agent/session joint checkpoint or production write barrier acceptance",
              "controlRoot": str(root), "repository": repository, "secretName": secret_name,
              "transport": "HTTP on private Docker kind network plus localhost9002; TLS not exercised",
              "minio": {"containerId": server["Id"], "imageId": server["Image"], "kindIp": endpoint,
                        "mounts": [{"type": m["Type"], "name": m.get("Name"), "destination": m["Destination"]} for m in server["Mounts"]]},
              "operations": [], "businessPods": [], "cleanup": []}
    active_pods = []
    owned = {}

    async def release(workspace):
        metadata = require_ok(await client.get_workspace(workspace))
        owner = metadata.get("owner", {})
        if owner.get("attemptId"):
            assert owner["taskId"] == owned[workspace]
            stopped = require_ok(await client.release_workspace(WorkspaceBinding.from_record(metadata).as_request()))
            assert stopped["owner"]["stopConfirmed"] is True
            report["cleanup"].append({"workspaceId": workspace, "stopConfirmed": True})

    async def prepare():
        workspace = new_id("workspace")
        require_ok(await client.prepare_workspace(workspace, "session-" + uuid4().hex[:16], new_id("branch")))
        owned[workspace] = new_id("task")
        return workspace

    async def claim(workspace):
        data = require_ok(await client.claim_workspace(workspace, owned[workspace], new_id("attempt")))
        return WorkspaceBinding.from_record(data).as_request()

    async def operation(workspace, action, *, checkpoint=None, snapshot=None, manifest=None):
        name_id = "probe-" + uuid4().hex[:16]
        args = [str(REPO / ".cache/workspace-backup-pod"), "-id", name_id, "-workspace", workspace,
                "-secret", secret_name, "-endpoint", endpoint, "-operation", action]
        for key, value in (("-checkpoint", checkpoint), ("-snapshot", snapshot), ("-manifest", manifest)):
            if value: args += [key, value]
        spec = json.loads(checked(args))
        kube_object("apply", spec["networkPolicy"])
        pod = kube_object("create", spec["pod"])
        name, uid = pod["metadata"]["name"], pod["metadata"]["uid"]
        active_pods.append((name, uid))
        started = time.perf_counter()
        async with asyncio.timeout(150):
            while True:
                pod = json.loads(checked([*KUBE, "-n", "sandboxd-storage", "get", "pod", name, "-o", "json"]))
                assert pod["metadata"]["uid"] == uid and pod["metadata"]["labels"]["sandbox.io/managed-by"] == "sandboxd-backup"
                if pod["status"]["phase"] in {"Succeeded", "Failed"}: break
                await asyncio.sleep(.2)
        status = pod["status"]["containerStatuses"][0]
        assert "terminated" in status["state"] and pod["spec"]["restartPolicy"] == "Never"
        # CRI info may include resolved secret environments: parse only runtime type, never persist the raw object.
        container_id = status["containerID"].split("://", 1)[1]
        cri = json.loads(checked(["docker", "exec", "sandboxd-control-plane", "crictl", "inspect", "-o", "json", container_id]))
        runtime_type = cri.get("info", {}).get("runtimeType", "")
        row = {"operation": action, "workspaceId": workspace, "podUid": uid, "podName": name,
               "runtimeClass": pod["spec"].get("runtimeClassName"), "runtimeType": runtime_type,
               "imageId": status["imageID"], "imageRef": pod["spec"]["containers"][0]["image"],
               "runAsUser": pod["spec"]["securityContext"]["runAsUser"], "readonlySource": pod["spec"]["containers"][0]["volumeMounts"][0].get("readOnly", False),
               "phase": pod["status"]["phase"], "exitCode": status["state"]["terminated"]["exitCode"],
               "elapsedMs": (time.perf_counter() - started) * 1000}
        report["operations"].append(row)
        assert row["runtimeClass"] == "gvisor" and "runsc" in runtime_type and row["runAsUser"] == 65532
        raw = checked([*KUBE, "-n", "sandboxd-storage", "logs", name, "-c", "sandbox"])
        result = json.loads(raw)
        row["result"] = result
        assert row["phase"] == "Succeeded" and row["exitCode"] == 0 and result["ok"] is True, result
        print(json.dumps({"operation": action, "passed": True, "podUid": uid}), flush=True)
        checked([*KUBE, "-n", "sandboxd-storage", "delete", "pod", name, "--wait=true", "--timeout=15s"])
        active_pods.remove((name, uid))
        return result

    try:
        await ready(process)
        source = await prepare()
        binding = await claim(source)
        values1 = {"report.md": "synthetic version one\n", "remove-synthetic.txt": "only this fixture may delete me\n"}
        for path, content in values1.items():
            require_ok(await client.workspace_files(binding, {"operation": "write_file", "path": path, "content": content}))
        await release(source)  # Component quiescence is real Go stop proof, not a claimed concurrent backup barrier.
        encoded = {"access-key": credentials["access"], "secret-key": credentials["secret"], "repository": repository, "password": password}
        kube_object("create", {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": secret_name, "namespace": "sandboxd-storage", "labels": {"sandbox.io/managed-by": "sandboxd-backup"}},
                               "type": "Opaque", "data": {k: base64.b64encode(v.encode()).decode() for k, v in encoded.items()}})
        await operation(source, "init")
        cp1, cp2 = "cp-" + uuid4().hex, "cp-" + uuid4().hex
        first = await operation(source, "backup", checkpoint=cp1)
        repeated = await operation(source, "backup", checkpoint=cp1)
        assert repeated["snapshotId"] == first["snapshotId"] and repeated["reusedSnapshot"] is True
        binding = await claim(source)
        values2 = {"report.md": "synthetic version two\n", "added.txt": "new synthetic file\n"}
        require_ok(await client.workspace_files(binding, {"operation": "write_file", "path": "report.md", "content": values2["report.md"], "expectedSha256": hashlib.sha256(values1["report.md"].encode()).hexdigest()}))
        require_ok(await client.workspace_files(binding, {"operation": "write_file", "path": "added.txt", "content": values2["added.txt"]}))
        await release(source)
        # Only delete this run's synthetic, hash-checked file, after its owner is stopped.
        target = "/export/workspaces/" + source + "/remove-synthetic.txt"
        assert checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum", target]).split()[0] == hashlib.sha256(values1["remove-synthetic.txt"].encode()).hexdigest()
        checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "rm", "--", target])
        second = await operation(source, "backup", checkpoint=cp2)
        assert first["repository"] == second["repository"]
        assert first["snapshotId"] != second["snapshotId"] and first["manifest"]["sha256"] != second["manifest"]["sha256"]
        for snapshot, expected in ((first, values1), (second, values2)):
            restored = await prepare()
            result = await operation(restored, "restore", snapshot=snapshot["snapshotId"], manifest=snapshot["manifest"]["sha256"])
            assert result["manifest"]["sha256"] == snapshot["manifest"]["sha256"]
            binding = await claim(restored)
            actual = require_ok(await client.workspace_files(binding, {"operation": "list_files", "path": "."}))
            assert actual["truncated"] is False
            assert {f["path"]: f["sha256"] for f in actual["entries"]} == {p: hashlib.sha256(v.encode()).hexdigest() for p, v in expected.items()}
            await release(restored)
        await operation(source, "check")
        await operation(source, "stats")
        report.update(passed=True, sourceWorkspaceId=source, firstSnapshotId=first["snapshotId"], secondSnapshotId=second["snapshotId"],
                      versionedNewModifiedDeletedFilesVerified=True, restoredToDistinctEmptyWorkspaces=True, duplicateBackendRequestReusedSnapshot=True,
                      businessPods=client.observations)
    except Exception as exc:
        report["error"] = public_error(exc)
        raise
    finally:
        for workspace in owned:
            try: await release(workspace)
            except Exception as exc: report["cleanup"].append({"workspaceId": workspace, "error": public_error(exc)})
        for name, uid in active_pods:
            # Keep unfinished Pods and state for reconciliation, never force-remove a writer.
            report["cleanup"].append({"maintenancePod": name, "podUid": uid, "retainedForInspection": True})
        await client.close()
        stop_go(process, log)
        report["timestamp"] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args.output))
    print(json.dumps({"passed": report["passed"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
