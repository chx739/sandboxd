"""可信、独立的V2部署初始化；标准库配置读取不加载Agent/模型。

初始化意图先落盘。INIT_SUBMITTED后只查原Pod；Pod缺失也不能证明未执行。
此入口不迁移已有仓库、不恢复控制盘丢失、不提供任意部署模板。
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
DEPLOYMENTS = Path.home() / ".local/share/sandboxd/deployments"
CREDENTIALS = Path.home() / ".local/share/sandboxd/workspace-backup/minio-credentials.json"
KUBE = ["kubectl", "--context", "kind-sandboxd"]
IMAGE = "sandboxd-restic:0.18.1-v3"
PHASES = {"PLANNED", "SECRET_READY", "INIT_SUBMITTED", "OWNER_READY", "READY"}
HASH = re.compile(r"[a-f0-9]{64}\Z")
CONTROLLER = re.compile(r"controller-[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}\Z")


class DeploymentError(RuntimeError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def directory(path):
    info = path.lstat()
    if (path != path.resolve(strict=True) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or str(path).startswith("/mnt/")):
        raise DeploymentError("requires private native control directory without symlinks")
    return {"directory": str(path), "device": info.st_dev, "inode": info.st_ino}


def private_read(path, limit=131072):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > limit):
            raise DeploymentError("configuration is not a private bounded regular file")
        return json.loads(handle.read(limit + 1))


def save(path, value):
    temporary = path.parent / (".publish-" + uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(encode(value) + b"\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def publish(root, state):
    save(root / "deployment.json", {"state": state, "sha256": hashlib.sha256(encode(state)).hexdigest()})


def root_path(path, *, create=False):
    path = Path(path)
    if (not path.is_absolute() or path.parent != DEPLOYMENTS
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", path.name)):
        raise DeploymentError("deployment must be one named child of native deployments directory")
    if create:
        DEPLOYMENTS.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory(DEPLOYMENTS)
        path.mkdir(mode=0o700, exist_ok=True)
    directory(path)
    return path


def owner_valid(owner):
    return (isinstance(owner, dict) and set(owner) == {"controllerId", "repositoryCryptoId", "ownerSnapshotId"}
            and isinstance(owner["controllerId"], str) and CONTROLLER.fullmatch(owner["controllerId"])
            and all(isinstance(owner[k], str) and HASH.fullmatch(owner[k]) for k in ("repositoryCryptoId", "ownerSnapshotId")))


def load(root):
    value = private_read(root / "deployment.json")
    state = value["state"]
    if set(value) != {"state", "sha256"} or value["sha256"] != hashlib.sha256(encode(state)).hexdigest():
        raise DeploymentError("deployment state checksum mismatch")
    if (state["schemaVersion"] != 1 or state["phase"] not in PHASES or state["root"] != directory(root)
            or state["goControl"] != directory(root / "go-control")
            or state["agentControl"] != directory(root / "agent-control")
            or state["backupImage"] != IMAGE or state["businessImage"] != "sandboxd-workspace:runtime-v1.5"
            or not CONTROLLER.fullmatch(state["controllerId"])
            or not re.fullmatch(r"restic-[a-f0-9]{32}", state["secretName"])
            or not re.fullmatch(r"init-[a-f0-9]{32}", state["jobId"])
            or not re.fullmatch(r"owned-deploy/[a-f0-9]{32}", state["prefix"])):
        raise DeploymentError("invalid or moved deployment")
    for key in ("nfsIp", "minioIp"):
        ipaddress.IPv4Address(state[key])
    if state["phase"] in {"OWNER_READY", "READY"} and (not owner_valid(state["owner"])
            or state["owner"]["controllerId"] != state["controllerId"]):
        raise DeploymentError("deployment owner identity mismatch")
    return state


@contextmanager
def locked(root):
    fd = os.open(root / "bootstrap.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise DeploymentError("bootstrap lock is not private")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def command(args, *, body=None, timeout=40):
    # stdout可能是Secret；异常不附加命令、响应或stderr。
    result = subprocess.run(args, input=None if body is None else encode(body), capture_output=True, timeout=timeout)
    if result.returncode:
        raise DeploymentError("fixed deployment operation failed; persisted intent retained")
    if len(result.stdout) > 2 << 20:
        raise DeploymentError("deployment response exceeds limit")
    return result.stdout


def get(kind, name, namespace="sandboxd-storage"):
    raw = command([*KUBE, "-n", namespace, "get", kind, name, "--ignore-not-found", "-o", "json"])
    return json.loads(raw) if raw.strip() else None


def contains(actual, expected):
    """允许API补默认字段；固定字段/数组及容器数量不能改变。"""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(contains(a, b) for a, b in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def create_or_match(body):
    old = get(body["kind"], body["metadata"]["name"])
    if old is None:
        old = json.loads(command([*KUBE, "create", "-f", "-", "-o", "json"], body=body))
    # Secret值只在可信内存核对，不进入deployment.json或公开日志。
    if not contains(old, body):
        raise DeploymentError("existing object differs; refusing adoption or overwrite")
    return old


def endpoint(container, project):
    template = '{"project":{{json (index .Config.Labels "com.docker.compose.project")}},"running":{{.State.Running}},"paused":{{.State.Paused}},"ip":{{json (index .NetworkSettings.Networks "kind").IPAddress}}}'
    value = json.loads(command(["docker", "inspect", "--format", template, container]))
    if value["project"] != project or not value["running"] or value["paused"]:
        raise DeploymentError("project storage container not healthy")
    return str(ipaddress.IPv4Address(value["ip"]))


def endpoints():
    return {"nfsIp": endpoint("sandboxd-workspace-nfs-1", "sandboxd-workspace"),
            "minioIp": endpoint("sandboxd-workspace-backup-minio-1", "sandboxd-workspace-backup")}


def initial_state(root, addresses):
    # 不把现有Go/Agent历史自动收编到新仓库。
    if set(p.name for p in root.iterdir()) != {"bootstrap.lock"}:
        raise DeploymentError("fresh deployment directory must contain no prior files")
    for child in ("go-control", "agent-control"):
        (root / child).mkdir(mode=0o700)
    state = {"schemaVersion": 1, "phase": "PLANNED", "root": directory(root),
             "goControl": directory(root / "go-control"), "agentControl": directory(root / "agent-control"),
             **addresses, "backupImage": IMAGE, "businessImage": "sandboxd-workspace:runtime-v1.5",
             "controllerId": "controller-" + uuid4().hex, "secretName": "restic-" + uuid4().hex,
             "jobId": "init-" + uuid4().hex, "prefix": "owned-deploy/" + uuid4().hex}
    publish(root, state)
    return state


def credentials(root, state):
    path = root / "repository-credentials.json"
    if not path.exists():
        # 只有远端Secret尚未提交的PLANNED才能生成新密码。
        if state["phase"] != "PLANNED":
            raise DeploymentError("repository credentials missing; do not rotate")
        values = private_read(CREDENTIALS, 4096)
        if set(values) != {"access", "secret"} or not all(isinstance(v, str) and v for v in values.values()):
            raise DeploymentError("private MinIO configuration invalid")
        save(path, {"access-key": values["access"], "secret-key": values["secret"],
                    "password": secrets.token_urlsafe(32), "repository": "s3:http://" + state["minioIp"] + ":9000/sandboxd-workspace/" + state["prefix"]})
    value = private_read(path, 8192)
    expected = "s3:http://" + state["minioIp"] + ":9000/sandboxd-workspace/" + state["prefix"]
    if (set(value) != {"access-key", "secret-key", "password", "repository"} or value["repository"] != expected
            or not all(isinstance(v, str) and v and len(v) <= 1024 for v in value.values())):
        raise DeploymentError("persisted repository credentials mismatch")
    return value


def pod_match(pod, state):
    template = state["podTemplate"]
    if (not contains(pod, template) or pod["metadata"].get("deletionTimestamp")
            or (state.get("podUid") and pod["metadata"]["uid"] != state["podUid"])):
        raise DeploymentError("initialization Pod identity/spec changed")


def stopped_proof(pod):
    statuses = pod.get("status", {}).get("containerStatuses", [])
    if (pod.get("status", {}).get("phase") != "Succeeded" or len(statuses) != 1
            or statuses[0].get("state", {}).get("terminated", {}).get("exitCode") != 0
            or not statuses[0].get("imageID")):
        raise DeploymentError("initialization did not finish successfully; do not re-init")
    node = pod["spec"]["nodeName"]
    current = get("node", node, "default")
    lease = get("lease", node, "kube-node-lease")
    renew = datetime.fromisoformat(lease["spec"]["renewTime"].replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - renew).total_seconds()
    if (not any(c["type"] == "Ready" and c["status"] == "True" for c in current["status"]["conditions"])
            or not 0 <= age <= 40):
        raise DeploymentError("node termination proof unavailable")
    return {"podUid": pod["metadata"]["uid"], "podName": pod["metadata"]["name"],
            "imageId": statuses[0]["imageID"], "nodeName": node, "leaseRenewTime": lease["spec"]["renewTime"]}


def bind_agent(root, state):
    # 只有init需要加载既有控制模块；正常go-args/agent-env保持标准库路径。
    from .app.runtime.checkpoints import CheckpointLedger
    from .app.runtime.session_refs import SessionReferences
    path = root / "agent-control/checkpoints"
    ledger = CheckpointLedger(path / "checkpoints.jsonl", SessionReferences(path))
    try:
        ledger.maintenance.bind(state["owner"])
    finally:
        ledger.close()


def cleanup_init(state):
    pod = get("pod", state["proof"]["podName"])
    if pod is None:
        return
    pod_match(pod, state)
    stopped_proof(pod)
    command([*KUBE, "delete", "--raw", "/api/v1/namespaces/sandboxd-storage/pods/" + pod["metadata"]["name"], "-f", "-"],
            body={"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": state["proof"]["podUid"]}})


def initialize(path):
    root = root_path(path, create=True)
    with locked(root):
        addresses = endpoints()
        state = load(root) if (root / "deployment.json").exists() else initial_state(root, addresses)
        if any(state[k] != addresses[k] for k in addresses):
            raise DeploymentError("storage endpoint changed; explicit migration required")
        values = credentials(root, state)
        secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "immutable": True,
                  "metadata": {"name": state["secretName"], "namespace": "sandboxd-storage",
                               "labels": {"sandbox.io/managed-by": "sandboxd-backup", "sandbox.io/controller": state["controllerId"]}},
                  "data": {k: base64.b64encode(v.encode()).decode() for k, v in values.items()}}
        create_or_match(secret)
        if state["phase"] == "PLANNED":
            spec = json.loads(command([str(REPO / ".cache/workspace-backup-pod-owned"), "-image", IMAGE,
                "-id", state["jobId"], "-workspace", "workspace-" + uuid4().hex, "-secret", state["secretName"],
                "-endpoint", state["minioIp"], "-operation", "init-owned", "-controller", state["controllerId"]]))
            for key in ("pod", "networkPolicy"):
                spec[key]["metadata"].pop("creationTimestamp", None)
                spec[key].pop("status", None)
            state.update(phase="SECRET_READY", podTemplate=spec["pod"], networkPolicy=spec["networkPolicy"])
            publish(root, state)
        if state["phase"] in {"SECRET_READY", "INIT_SUBMITTED"}:
            create_or_match(state["networkPolicy"])
            pod = get("pod", state["podTemplate"]["metadata"]["name"])
            if state["phase"] == "SECRET_READY":
                if pod is not None:
                    raise DeploymentError("unexpected initialization Pod before submit intent")
                state["phase"] = "INIT_SUBMITTED"; publish(root, state)
                # 仅此首次活进程沿刚持久化的意图提交一次。超时后只查询同名Pod。
                pod = json.loads(command([*KUBE, "create", "-f", "-", "-o", "json"], body=state["podTemplate"]))
            if pod is None:
                raise DeploymentError("submitted init Pod missing; outcome unknown, no re-init")
            pod_match(pod, state)
            if "podUid" not in state:
                state["podUid"] = pod["metadata"]["uid"]; publish(root, state)
            deadline = time.monotonic() + 130
            while pod.get("status", {}).get("phase") not in {"Succeeded", "Failed"}:
                if time.monotonic() >= deadline:
                    raise DeploymentError("initialization wait timed out; reconcile same Pod")
                time.sleep(.3)
                pod = get("pod", state["podTemplate"]["metadata"]["name"])
                if pod is None:
                    raise DeploymentError("initialization Pod disappeared; outcome unknown")
                pod_match(pod, state)
            proof = stopped_proof(pod)
            result = json.loads(command([*KUBE, "-n", "sandboxd-storage", "logs", pod["metadata"]["name"]]))
            if (result.get("ok") is not True or result.get("operation") != "init-owned"
                    or result.get("resticVersion") != "0.18.1" or not owner_valid(result.get("owner"))
                    or result["owner"]["controllerId"] != state["controllerId"]):
                raise DeploymentError("initialization result invalid")
            state.update(phase="OWNER_READY", owner=result["owner"], proof=proof)
            publish(root, state)  # 先固定成功证明，才可绑定控制目录/删除Pod。
        if state["phase"] == "OWNER_READY":
            bind_agent(root, state)
            state["phase"] = "READY"; publish(root, state)
        cleanup_init(state)
        return ready(root)


def ready(path):
    root = root_path(path)
    state = load(root)
    if state["phase"] != "READY":
        raise DeploymentError("deployment is not ready; reconcile initialization first")
    # 目录claim必须仍与原控制目录/Owner一致；不打开或更改活跃Agent账本。
    checkpoint = root / "agent-control/checkpoints"
    expected = {"schemaVersion": 1, "owner": state["owner"], **directory(checkpoint)}
    claim = Path.home() / ".local/share/sandboxd/repository-controllers" / (state["owner"]["repositoryCryptoId"] + ".json")
    if private_read(claim, 4096) != expected:
        raise DeploymentError("repository control-directory claim differs")
    return state


def view(state, kind):
    owner = state["owner"]
    if kind == "go-args":
        return ["-workspace-control-dir", state["goControl"]["directory"], "-workspace-backup-image", IMAGE,
                "-workspace-backup-secret", state["secretName"], "-workspace-backup-minio-ip", state["minioIp"],
                "-workspace-backup-controller", owner["controllerId"], "-workspace-backup-repository-id", owner["repositoryCryptoId"],
                "-workspace-backup-owner-snapshot", owner["ownerSnapshotId"]]
    if kind == "agent-env":
        return ["AGENTD_RECOVERY_DIR=" + state["agentControl"]["directory"], "AGENTD_CHECKPOINT_MODE=restic",
                "AGENTD_CHECKPOINT_REPOSITORY_ID=repo-" + owner["controllerId"][11:]]
    raise DeploymentError("unknown configuration view")


def main():
    parser = argparse.ArgumentParser(description="固定V2新部署初始化与标准库配置视图；不是Agent工具")
    parser.add_argument("command", choices=("init", "status", "go-args", "agent-env"))
    parser.add_argument("--deployment-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "init":
            state = initialize(args.deployment_dir)
        elif args.command == "status":
            state = load(root_path(args.deployment_dir))
        else:
            state = ready(args.deployment_dir)
        if args.command == "go-args" and any(state[k] != v for k, v in endpoints().items()):
            raise DeploymentError("storage endpoint changed; refusing startup")
        if args.command in {"go-args", "agent-env"}:
            rows = view(state, args.command)
            if any("\n" in x or "\r" in x or "\0" in x for x in rows):
                raise DeploymentError("invalid line configuration")
            print("\n".join(rows))
        else:
            print(json.dumps({"phase": state["phase"], "owner": state.get("owner"), "secretName": state["secretName"],
                              "root": state["root"], "nfsIp": state["nfsIp"], "minioIp": state["minioIp"]}, indent=2))
    except DeploymentError as exc:
        # 本模块自定义错误只使用固定说明，不附加后端/凭据内容。
        parser.exit(1, "deployment stopped: " + str(exc) + "; preserve original directory and reconcile same intent\n")
    except Exception:
        parser.exit(1, "deployment operation stopped; preserve original directory and reconcile same intent (no automatic re-init)\n")


if __name__ == "__main__":
    main()
