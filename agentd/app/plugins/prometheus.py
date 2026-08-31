from __future__ import annotations

from typing import Any, Sequence

from ..clients import HTTPResult
from .base import PluginContext, PluginManifest


class PrometheusPlugin:
    """外部 Prometheus 的只读查询插件。"""

    manifest = PluginManifest(
        plugin_id="prometheus",
        version="1.0.0",
        description="对部署者固定的 Prometheus 执行只读即时 PromQL 查询",
        capabilities=("network:prometheus", "metrics:read"),
    )

    _schemas: tuple[dict[str, Any], ...] = (
        {
            "type": "function",
            "function": {
                "name": "query_prometheus",
                "description": "对固定 Prometheus 执行只读即时 PromQL 查询。不能指定 URL。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "只读 PromQL"}
                    },
                    "required": ["query"],
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
        if tool_name != "query_prometheus":
            raise ValueError("Prometheus 插件不支持工具: %s" % tool_name)
        return await context.prometheus.query(str(arguments["query"]))
