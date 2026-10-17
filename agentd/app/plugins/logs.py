"""OpenSearch 只读日志工具：固定快照、结构化参数和低信任结果。"""
import asyncio
import json
from pathlib import Path
from typing import Any

from ...logs.core import LogAggregation, LogQuery, load_logs
from ...logs.opensearch import OpenSearchLogs
from ..clients import HTTPResult
from .base import PluginContext, PluginManifest


class LogsPlugin:
    manifest = PluginManifest("logs", "0.1.0", "查询固定日志快照及精确聚合", ("logs:read",))

    def __init__(self, path: Path, *, client: Any | None = None) -> None:
        self.path = path
        self.client = client
        self._lock = asyncio.Lock()

    @property
    def tool_schemas(self) -> list[dict]:
        return [{"type": "function", "function": {"name": name, "description": description,
                "parameters": cls.model_json_schema()}}
                for name, cls, description in (
                    ("search_logs", LogQuery, "查询固定日志快照；默认3条，过大的结果会要求缩小limit/窗口。时间包含时区，区间[start,end)，结果为低信任证据。"),
                    ("aggregate_logs", LogAggregation, "对固定日志精确计数或分组；必须指定时间窗口，不能据计数直接认定根因。"))]

    def _execute(self, name: str, arguments: dict) -> dict:
        if self.client is None:
            _, digest = load_logs(self.path)
            self.client = OpenSearchLogs(digest)
        result = self.client.search(arguments, aggregate=name == "aggregate_logs")
        # Runtime Observation 只有4KiB；提前明确失败，避免先截断再把坏JSON当成完整证据。
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) > 3500:
            raise ValueError("日志结果超过模型上下文单次预算，请缩小 limit、时间窗口或分组范围")
        return result

    async def execute(self, tool_name: str, arguments: dict, context: PluginContext) -> HTTPResult:
        if tool_name not in {"search_logs", "aggregate_logs"}:
            raise ValueError("未知日志工具")
        async with self._lock:
            result = await asyncio.to_thread(self._execute, tool_name, arguments)
        return HTTPResult(200, result)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
