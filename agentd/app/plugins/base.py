from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from ..clients import HTTPResult, PrometheusClient, SandboxdClient


@dataclass(frozen=True)
class PluginManifest:
    """插件向 Runtime 声明的静态身份和能力。

    capabilities 只是给审计和前置检查看的“声明”，不是授权。真正的授权仍由
    Python Policy、sandboxd、RBAC 和审批门共同决定，避免插件自报权限后越权。
    """

    plugin_id: str
    version: str
    description: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class PluginContext:
    """一次工具执行所需的最小上下文。

    插件只拿到已经构造好的窄接口 Client，不接触 Token、Operator 凭据、任意
    文件系统或通用网络 Client。这是运维 Agent 与普通本地插件系统的关键区别。
    """

    sandbox_id: str
    prometheus: PrometheusClient
    sandboxd: SandboxdClient


class ToolPlugin(Protocol):
    manifest: PluginManifest

    @property
    def tool_schemas(self) -> Sequence[dict[str, Any]]: ...

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> HTTPResult: ...
