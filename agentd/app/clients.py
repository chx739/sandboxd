from __future__ import annotations

import asyncio
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

MAX_HTTP_BODY_BYTES = 64 << 10
MAX_SSH_OUTPUT_BYTES = 32 << 10
LINUX_READ_OPERATIONS = {
    "host_summary",
    "process_list",
    "disk_usage",
    "read_demo_log",
}
_TARGET_ID = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,62})$")


@dataclass(frozen=True)
class HTTPResult:
    status_code: int
    body: Any

    def as_tool_text(self) -> str:
        return json.dumps(
            {"statusCode": self.status_code, "body": self.body},
            ensure_ascii=False,
            separators=(",", ":"),
        )


async def _bounded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> HTTPResult:
    async with client.stream(method, url, **kwargs) as response:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > MAX_HTTP_BODY_BYTES:
                raise ValueError("HTTP 响应超过 64 KiB 上限")
            body.extend(chunk)

        if not body:
            parsed: Any = None
        else:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = body.decode("utf-8", errors="replace")
        return HTTPResult(status_code=response.status_code, body=parsed)


class PrometheusClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))

    async def query(self, promql: str) -> HTTPResult:
        return await _bounded_request(
            self._client,
            "GET",
            self._base_url + "/api/v1/query",
            params={"query": promql},
        )

    async def close(self) -> None:
        await self._client.aclose()


class SandboxdClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url
        self._headers = {"Authorization": "Bearer " + token}
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0))

    async def claim(self) -> dict[str, Any]:
        result = await _bounded_request(
            self._client,
            "POST",
            self._base_url + "/api/v1/sandboxes",
            headers=self._headers,
            timeout=190.0,
        )
        if result.status_code != 201 or not isinstance(result.body, dict):
            raise RuntimeError("认领 sandbox 失败，HTTP %d" % result.status_code)
        return result.body

    async def release(self, sandbox_id: str) -> None:
        result = await _bounded_request(
            self._client,
            "DELETE",
            self._base_url + "/api/v1/sandboxes/" + sandbox_id,
            headers=self._headers,
        )
        if result.status_code not in {204, 404}:
            raise RuntimeError("释放 sandbox 失败，HTTP %d" % result.status_code)

    async def kubernetes_read(
        self,
        sandbox_id: str,
        arguments: dict[str, Any],
    ) -> HTTPResult:
        return await _bounded_request(
            self._client,
            "POST",
            self._base_url
            + "/api/v1/sandboxes/"
            + sandbox_id
            + "/diagnostics/kubernetes",
            headers=self._headers,
            json=arguments,
        )

    async def propose_plan(self, arguments: dict[str, Any]) -> HTTPResult:
        return await _bounded_request(
            self._client,
            "POST",
            self._base_url + "/api/v1/plans",
            headers=self._headers,
            json=arguments,
        )

    async def close(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class LinuxTargetConfig:
    """部署者预先登记的 SSH 目标；这些字段永远不进入 Tool Schema。"""

    target_id: str
    host: str
    port: int
    user: str
    identity_file: Path
    known_hosts_file: Path


def _private_regular_file(path: Path, label: str) -> Path:
    """拒绝软链接和组/其他用户可读写文件，避免凭据路径被静默替换。"""

    if not path.is_absolute():
        raise ValueError("%s 必须是绝对路径" % label)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("%s 必须是普通文件且不能是符号链接" % label)
    if info.st_mode & 0o077:
        raise ValueError("%s 权限必须为 0600 或更严格" % label)
    return path.resolve(strict=True)


def load_linux_targets(path: Path | None) -> dict[str, LinuxTargetConfig]:
    """从可信配置文件加载静态 Target Registry，不支持运行时在线注册。"""

    if path is None:
        return {}
    config_path = _private_regular_file(path, "Linux Target 配置")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"targets"}:
        raise ValueError("Linux Target 配置只能包含 targets")
    items = payload["targets"]
    if not isinstance(items, list) or len(items) > 16:
        raise ValueError("Linux Target 数量必须在 0 到 16 之间")

    targets: dict[str, LinuxTargetConfig] = {}
    expected = {
        "targetId",
        "host",
        "port",
        "user",
        "identityFile",
        "knownHostsFile",
    }
    for raw in items:
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError("Linux Target 字段不完整或包含未知字段")
        target_id = raw["targetId"]
        host = raw["host"]
        user = raw["user"]
        port = raw["port"]
        if not isinstance(target_id, str) or not _TARGET_ID.fullmatch(target_id):
            raise ValueError("targetId 格式无效")
        if target_id in targets:
            raise ValueError("重复 targetId: %s" % target_id)
        # host/user 虽然来自部署者而非模型，仍拒绝空白和控制字符，防止污染 argv/日志。
        if (
            not isinstance(host, str)
            or not host
            or len(host) > 253
            or host.startswith("-")
            or not re.fullmatch(r"[A-Za-z0-9._:-]+", host)
        ):
            raise ValueError("Linux Target host 无效")
        if not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
            raise ValueError("Linux Target user 无效")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Linux Target port 无效")
        targets[target_id] = LinuxTargetConfig(
            target_id=target_id,
            host=host,
            port=port,
            user=user,
            identity_file=_private_regular_file(
                Path(str(raw["identityFile"])),
                "SSH 私钥",
            ),
            known_hosts_file=_private_regular_file(
                Path(str(raw["knownHostsFile"])),
                "known_hosts",
            ),
        )
    return targets


async def _read_bounded(
    stream: asyncio.StreamReader,
    limit: int,
) -> bytes:
    value = bytearray()
    while True:
        chunk = await stream.read(min(4096, limit + 1 - len(value)))
        if not chunk:
            return bytes(value)
        value.extend(chunk)
        if len(value) > limit:
            raise ValueError("SSH 输出超过 %d KiB 上限" % (limit >> 10))


class LinuxHostClient:
    """用固定 ssh argv 访问静态目标，绝不拼 shell 字符串或接受模型命令。"""

    def __init__(self, targets: dict[str, LinuxTargetConfig]) -> None:
        self._targets = dict(targets)

    @classmethod
    def from_file(cls, path: Path | None) -> "LinuxHostClient":
        return cls(load_linux_targets(path))

    async def read(self, target_id: str, operation: str) -> HTTPResult:
        target = self._targets.get(target_id)
        if target is None:
            return HTTPResult(
                403,
                {"denyLayer": "connector-policy", "error": "未知 Linux targetId"},
            )
        # 这是独立于 Agent Policy 的第二道白名单；直调 Connector 也不能变成任意 SSH。
        if operation not in LINUX_READ_OPERATIONS:
            return HTTPResult(
                403,
                {"denyLayer": "connector-policy", "error": "Linux operation 不在只读白名单"},
            )

        argv = (
            "/usr/bin/ssh",
            "-F",
            "/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + os.fspath(target.known_hosts_file),
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "RequestTTY=no",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "ServerAliveInterval=3",
            "-o",
            "ServerAliveCountMax=1",
            "-i",
            os.fspath(target.identity_file),
            "-p",
            str(target.port),
            "--",
            "%s@%s" % (target.user, target.host),
            operation,
        )
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=4096,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(
            _read_bounded(process.stdout, MAX_SSH_OUTPUT_BYTES)
        )
        stderr_task = asyncio.create_task(
            _read_bounded(process.stderr, 8 << 10)
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task),
                timeout=10,
            )
            exit_code = await asyncio.wait_for(process.wait(), timeout=2)
        except BaseException:
            process.kill()
            await process.wait()
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            raise

        body = {
            "targetId": target_id,
            "operation": operation,
            "exitCode": exit_code,
            "stdout": stdout.decode("utf-8", errors="replace"),
        }
        if exit_code != 0:
            # OpenSSH stderr 可能含私钥/known_hosts 路径，不能进入模型、Trace 或 Session。
            body["error"] = "SSH operation failed"
            return HTTPResult(502, body)
        return HTTPResult(200, body)
