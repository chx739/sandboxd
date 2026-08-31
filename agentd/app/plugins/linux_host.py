from __future__ import annotations

from typing import Any, Sequence

from ..clients import HTTPResult
from .base import PluginContext, PluginManifest


class LinuxHostPlugin:
    """把通用 SSH 收缩为 targetId + 四个只读 operation。"""

    manifest = PluginManifest(
        plugin_id="linux-host",
        version="1.0.0",
        description="通过固定 SSH Target Registry 执行四个只读 Linux 诊断操作",
        capabilities=("network:ssh", "linux:read"),
    )

    _schemas: tuple[dict[str, Any], ...] = (
        {
            "type": "function",
            "function": {
                "name": "linux_read",
                "description": (
                    "读取部署者预先登记的 Linux 目标；只允许 host_summary、"
                    "process_list、disk_usage、read_demo_log。不能传主机、用户、路径或命令。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "targetId": {"type": "string"},
                        "operation": {
                            "type": "string",
                            "enum": [
                                "host_summary",
                                "process_list",
                                "disk_usage",
                                "read_demo_log",
                            ],
                        },
                    },
                    "required": ["targetId", "operation"],
                    "additionalProperties": False,
                },
            },
        },
    )

    @property
    def tool_schemas(self) -> Sequence[dict[str, Any]]:
        return self._schemas

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> HTTPResult:
        if tool_name != "linux_read":
            raise ValueError("Linux Host 插件不支持工具: %s" % tool_name)
        return await context.linux_hosts.read(
            str(arguments["targetId"]),
            str(arguments["operation"]),
        )
