"""管理实验专用真实restic排他锁；不注册Agent工具，不提供unlock。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from .workspace_demo import REPO, checked

RESTIC_SHA256 = "01143daba61a1dc8afb0cb3d03ba83e6b118f55b6b146b27473a09efc3df13d6"


class RepositoryLock:
    def __init__(self, root: Path, secret: str, endpoint: str):
        expected = Path.home() / ".local/share/sandboxd/workspace-storage-fault"
        if root.resolve().parent != expected.resolve() or not re.fullmatch(r"[a-f0-9]{32}", root.name):
            raise ValueError("lock fixture requires its own private native root")
        if not re.fullmatch(r"restic-[a-f0-9]{16}", secret):
            raise ValueError("requires project fixture Secret name")
        self.root, self.secret, self.endpoint = root, secret, endpoint
        self.expected_repository_id = None
        self.process = self.watchdog = None
        self.lock_ids = []
        self.command = self.env = None
        self.container_id = None  # 与原MinIO夹具报告兼容；此实验不暂停服务。

    def execute(self, *args):
        value = subprocess.run(self.command + list(args), env=self.env, capture_output=True,
                               text=True, timeout=15)
        if value.returncode:
            # restic stderr可能包含后端凭据，只报告退出码。
            raise RuntimeError(f"trusted lock observation failed, exit={value.returncode}")
        return value.stdout

    def locks(self):
        ids = self.execute("list", "locks").splitlines()
        if not all(re.fullmatch(r"[a-f0-9]{64}", value) for value in ids):
            raise ValueError("unexpected restic lock identities")
        return sorted(ids)

    def begin(self):
        binary = REPO / ".cache/restic-image-v2/restic"
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == RESTIC_SHA256
        secret = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-storage",
                                     "get", "secret", self.secret, "-o", "json"]))
        assert secret["metadata"]["labels"]["sandbox.io/managed-by"] == "sandboxd-backup"
        values = {k: base64.b64decode(secret["data"][k]).decode() for k in
                  ("repository", "password", "access-key", "secret-key")}
        prefix = "s3:http://" + self.endpoint + ":9000/sandboxd-workspace/"
        assert values["repository"].startswith(prefix)
        server = json.loads(checked(["docker", "inspect", "sandboxd-workspace-backup-minio-1"]))[0]
        assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace-backup"
        assert server["State"]["Running"] and not server["State"]["Paused"]
        assert server["NetworkSettings"]["Networks"]["kind"]["IPAddress"] == self.endpoint
        assert {"HostIp": "127.0.0.1", "HostPort": "9002"} in server["NetworkSettings"]["Ports"]["9000/tcp"]
        # Docker Desktop桥接IP不对WSL管理进程直通；使用同容器已发布的固定loopback。
        # 下方再核对crypto ID，端口别名不能把另一个仓库当成来源。
        values["repository"] = "s3:http://127.0.0.1:9002/sandboxd-workspace/" + values["repository"][len(prefix):]
        for name in ("repository", "password"):
            fd = os.open(self.root / ("lock-" + name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(values[name]); stream.flush(); os.fsync(stream.fileno())
        self.command = [str(binary), "--no-cache", "--repository-file", str(self.root / "lock-repository"),
                        "--password-file", str(self.root / "lock-password"), "-o", "s3.connections=2",
                        "-o", "s3.bucket-lookup=path"]
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.root),
                    "AWS_ACCESS_KEY_ID": values["access-key"], "AWS_SECRET_ACCESS_KEY": values["secret-key"],
                    "AWS_DEFAULT_REGION": "us-east-1"}
        config = json.loads(self.execute("cat", "config"))
        assert config["id"] == self.expected_repository_id, "lock must target exact committed repository"
        assert not self.locks(), "existing locks belong to another operation; do not unlock them"
        self.process = subprocess.Popen(self.command + ["check", "--read-data", "--limit-download", "8"],
                                        env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        # PID加内核starttime避免复用；只给本夹具的check进程CONT/INT，不强杀或unlock。
        starttime = Path(f"/proc/{self.process.pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
        code = ("import os,pathlib,signal,sys,time;time.sleep(150);p=int(sys.argv[1]);"
                "f=pathlib.Path('/proc/'+str(p)+'/stat');"
                "s=f.read_text().rsplit(') ',1)[1].split()[19] if f.exists() else '';"
                "(os.kill(p,signal.SIGCONT),os.kill(p,signal.SIGINT)) if s==sys.argv[2] else None")
        self.watchdog = subprocess.Popen([sys.executable, "-c", code, str(self.process.pid), starttime],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            assert self.process.poll() is None, "check completed before lock could be held"
            ids = self.locks()
            if ids:
                assert len(ids) == 1
                os.kill(self.process.pid, signal.SIGSTOP)
                _, status = os.waitpid(self.process.pid, os.WUNTRACED)
                assert os.WIFSTOPPED(status)
                self.lock_ids = ids
                self.assert_active()
                return
            time.sleep(.05)
        raise TimeoutError("exclusive check lock not observed in fixture bound")

    def assert_active(self):
        assert self.process is not None and self.process.poll() is None
        status = Path(f"/proc/{self.process.pid}/status").read_text()
        assert re.search(r"^State:\s+T", status, re.MULTILINE), "check must remain stopped/live"
        assert self.locks() == self.lock_ids and len(self.lock_ids) == 1

    def end(self):
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGCONT)
            self.process.send_signal(signal.SIGINT)
            self.process.wait(timeout=15)
        if self.command:
            assert not self.locks(), "normal check shutdown did not release its lock; no forced unlock"
        if self.watchdog:
            self.watchdog.terminate()
            self.watchdog.wait(timeout=5)
