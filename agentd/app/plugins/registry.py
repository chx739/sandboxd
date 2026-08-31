from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from ..clients import HTTPResult
from .base import PluginContext, PluginManifest, ToolPlugin
from .files import FileToolsPlugin
from .kubernetes import KubernetesPlugin
from .linux_host import LinuxHostPlugin
from .prometheus import PrometheusPlugin


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    schema: dict[str, Any]
    plugin: ToolPlugin


class PluginRegistry:
    """只接受调用方显式传入实例的受信任插件注册表。

    这里故意没有 entry point、importlib、目录扫描或热加载。对运维 Agent 来说，
    “能装任意代码”与“插件即拥有宿主权限”几乎等价，不适合作为安全 Demo 默认值。
    """

    def __init__(self, plugins: Iterable[ToolPlugin]) -> None:
        self._plugins: dict[str, ToolPlugin] = {}
        self._tools: dict[str, RegisteredTool] = {}

        for plugin in plugins:
            manifest = plugin.manifest
            if manifest.plugin_id in self._plugins:
                raise ValueError("重复插件 id: %s" % manifest.plugin_id)
            self._plugins[manifest.plugin_id] = plugin

            for schema in plugin.tool_schemas:
                name = self._tool_name(schema)
                if name in self._tools:
                    raise ValueError("重复工具名: %s" % name)
                self._tools[name] = RegisteredTool(
                    name=name,
                    schema=deepcopy(schema),
                    plugin=plugin,
                )

        if not self._tools:
            raise ValueError("Plugin Registry 至少需要一个工具")

    @staticmethod
    def _tool_name(schema: dict[str, Any]) -> str:
        function = schema.get("function")
        if not isinstance(function, dict):
            raise ValueError("工具 Schema 缺少 function")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("工具 Schema 缺少合法 name")
        return name

    @property
    def tool_schemas(self) -> list[dict[str, Any]]:
        # Provider 适配器可能规范化传入对象，因此每次返回副本，保护注册表静态定义。
        return [deepcopy(item.schema) for item in self._tools.values()]

    @property
    def manifests(self) -> list[PluginManifest]:
        return [plugin.manifest for plugin in self._plugins.values()]

    def describe_plugins(self) -> list[dict[str, Any]]:
        """返回可公开给已认证调用方的静态信息，不包含 Client 或凭据。"""

        return [
            {
                "id": plugin.manifest.plugin_id,
                "version": plugin.manifest.version,
                "description": plugin.manifest.description,
                "capabilities": list(plugin.manifest.capabilities),
                "tools": [self._tool_name(schema) for schema in plugin.tool_schemas],
            }
            for plugin in self._plugins.values()

        ]
    def resolve(self, tool_name: str) -> RegisteredTool | None:
        return self._tools.get(tool_name)

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> HTTPResult:
        registered = self.resolve(tool_name)
        if registered is None:
            raise ValueError("未知工具: %s" % tool_name)
        return await registered.plugin.execute(tool_name, arguments, context)


def build_builtin_registry() -> PluginRegistry:
    """显式列出可信插件；代码审查可以一眼看到 Agent 的全部扩展面。"""

    return PluginRegistry(
        [
            PrometheusPlugin(),
            KubernetesPlugin(),
            LinuxHostPlugin(),
            FileToolsPlugin(),
        ]
    )
