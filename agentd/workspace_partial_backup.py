"""仅本轮合成文件的真实不可读竞态；不修改生产helper/镜像/工作区权限模型。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from .workspace_demo import checked
from .workspace_repository_lock import RepositoryLock

READABLE = "synthetic readable sibling; must survive partial backup\n"


class PartialBackup:
    def __init__(self, root: Path, secret: str, endpoint: str, minio, report_content: str):
        self.repository = RepositoryLock(root, secret, endpoint)
        self.minio = minio
        self.expected_repository_id = self.previous_snapshot_id = None
        self.workspace_id = self.checkpoint_id = None
        self.business_pod_uid = None
        self.expected_sha256 = hashlib.sha256(report_content.encode()).hexdigest()
        self.container_id = self.path = self.original_mode = None
        self.watchdog = None
        self.mode_dirty = False
        self.observed_snapshots_before_chmod = False
        self.pod_read_denied = False
        self.mode_at_failure = None

    def mode(self):
        return checked(["docker", "exec", self.container_id,
                        "stat", "-c", "%a", self.path]).strip()

    def begin(self):
        if not isinstance(self.workspace_id, str) or not re.fullmatch(r"workspace-[a-f0-9]{32}", self.workspace_id):
            raise ValueError("only the current fixture workspace may be changed")
        self.repository.expected_repository_id = self.expected_repository_id
        self.repository.configure_repository()
        info = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
        assert info["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
        assert info["State"]["Running"] and not info["State"]["Paused"]
        self.nfs_container_id = info["Id"]
        self.nfs_path = "/export/workspaces/" + self.workspace_id + "/report.md"
        assert isinstance(self.business_pod_uid, str) and re.fullmatch(r"[a-f0-9-]{36}", self.business_pod_uid)
        node = json.loads(checked(["docker", "inspect", "sandboxd-control-plane"]))[0]
        assert node["Config"]["Labels"]["io.x-k8s.kind.cluster"] == "sandboxd"
        self.container_id = node["Id"]
        self.path = ("/var/lib/kubelet/pods/" + self.business_pod_uid + "/volumes/kubernetes.io~nfs/sandboxd-"
                     + self.workspace_id + "/report.md")
        mount = json.loads(checked(["docker", "exec", self.container_id, "findmnt", "--json", "--target", self.path,
                                   "--output", "SOURCE,FSTYPE,OPTIONS"]))["filesystems"][0]
        assert mount["fstype"] == "nfs4" and mount["source"].endswith(":/workspaces/" + self.workspace_id)
        assert "rw" in mount["options"].split(",")
        actual = checked(["docker", "exec", self.container_id, "sha256sum", self.path]).split()[0]
        assert actual == self.expected_sha256, "only hash-verified synthetic file may be changed"
        self.original_mode = self.mode()
        assert re.fullmatch(r"[0-7]{3,4}", self.original_mode) and int(self.original_mode, 8) & 0o400
        # 先停存储响应，等待helper完成前置manifest、进入真实snapshots子命令。
        self.minio.begin()

    async def after_start(self, barrier: dict):
        assert barrier["checkpointId"] == self.checkpoint_id
        assert barrier["access"]["workspaceId"] == self.workspace_id
        command = ["kubectl", "--context", "kind-sandboxd", "--request-timeout=3s", "-n", "sandboxd-storage",
                   "exec", barrier["podName"], "--"]
        try:
            async with asyncio.timeout(25):
                while True:
                    try:
                        value = subprocess.run(command + ["ps", "-o", "pid,comm"], capture_output=True, text=True, timeout=4)
                        observed = False
                        for line in value.stdout.splitlines() if value.returncode == 0 else []:
                            fields = line.split()
                            if len(fields) == 2 and fields[1] == "restic" and re.fullmatch(r"[0-9]{1,10}", fields[0]):
                                # BusyBox的ps参数可能被终端宽度截断，只读取该restic PID的argv。
                                args = subprocess.run(command + ["cat", "/proc/" + fields[0] + "/cmdline"],
                                                      capture_output=True, text=True, timeout=4)
                                observed = (args.returncode == 0 and args.stdout.split("\0")[-4:] ==
                                            ["snapshots", "--tag", "checkpoint:" + self.checkpoint_id, ""])
                                if observed:
                                    break
                        if observed:
                            break
                    except subprocess.TimeoutExpired:
                        pass
                    await asyncio.sleep(.1)
            self.observed_snapshots_before_chmod = True
            restore = ["docker", "exec", "--user", "65532:65532", self.nfs_container_id,
                       "chmod", self.original_mode, self.nfs_path]
            # 固定argv恢复本文件权限；即使主实验退出也保留独立保险，不删除任何文件。
            code = ("import json,subprocess,sys,time;time.sleep(150);"
                    "subprocess.run(json.loads(sys.argv[1]),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)")
            self.watchdog = subprocess.Popen([sys.executable, "-c", code, json.dumps(restore)],
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.mode_dirty = True  # 命令确认丢失时也必须尝试恢复原权限。
            # 经现有业务Pod的真实NFS客户端改元数据，避免绕过Ganesha属性缓存。
            checked(["docker", "exec", self.container_id, "chmod", "000", self.path])
            assert self.mode() == "0"
            async with asyncio.timeout(15):
                while True:
                    denied = subprocess.run(command + ["cat", "/workspace/report.md"], capture_output=True, text=True, timeout=4)
                    sibling = subprocess.run(command + ["cat", "/workspace/evidence.txt"], capture_output=True, text=True, timeout=4)
                    if denied.returncode != 0 and "permission denied" in denied.stderr.lower() and sibling.returncode == 0 and sibling.stdout == READABLE:
                        self.pod_read_denied = True
                        break
                    await asyncio.sleep(.3)
        finally:
            # 现在放行真实restic，只有本合成文件保持不可读。
            self.minio.end()

    def verify_partial(self):
        assert not self.mode_dirty and self.mode_at_failure == "000" and self.pod_read_denied and self.observed_snapshots_before_chmod
        snapshots = json.loads(self.repository.execute("--json", "snapshots", "--tag", "checkpoint:" + self.checkpoint_id))
        assert len(snapshots) == 1 and re.fullmatch(r"[a-f0-9]{64}", snapshots[0]["id"])
        snapshot = snapshots[0]["id"]
        nodes = [json.loads(line) for line in self.repository.execute("--json", "ls", snapshot).splitlines()]
        paths = {n.get("path") for n in nodes if n.get("struct_type") == "node" and n.get("type") == "file"}
        assert "/workspace/evidence.txt" in paths and "/workspace/report.md" not in paths
        readable = self.repository.execute("dump", snapshot, "/workspace/evidence.txt")
        assert readable == READABLE
        previous = self.repository.execute("dump", self.previous_snapshot_id, "/workspace/report.md")
        assert hashlib.sha256(previous.encode()).hexdigest() == self.expected_sha256
        return {"orphanPartialSnapshotId": snapshot, "readableFilePresent": True,
                "unreadableReportAbsent": True, "previousSnapshotReportSha256": self.expected_sha256,
                "snapshotQueryObservedAfterManifest": True, "modeDuringFailure": "000",
                "maintenancePodReadDeniedBeforeBackup": True,
                "scope": "uncommitted incomplete snapshot retained; not a valid restore point"}

    def restore_mode(self):
        if self.mode_dirty:
            present = subprocess.run(["docker", "exec", self.container_id, "test", "-e", self.path],
                                     capture_output=True, timeout=5).returncode == 0
            if present:
                checked(["docker", "exec", self.container_id, "chmod", self.original_mode, self.path])
                assert self.mode() == self.original_mode
            else:
                checked(["docker", "exec", "--user", "65532:65532", self.nfs_container_id,
                         "chmod", self.original_mode, self.nfs_path])
                assert checked(["docker", "exec", "--user", "65532:65532", self.nfs_container_id,
                                "stat", "-c", "%a", self.nfs_path]).strip() == self.original_mode
            self.mode_dirty = False
        if self.watchdog:
            self.watchdog.terminate()
            self.watchdog.wait(timeout=5)
            self.watchdog = None

    def end(self):
        try:
            self.restore_mode()
        finally:
            self.minio.end()
            self.repository.end()
