from __future__ import annotations

from typing import Any, Sequence

from ..clients import HTTPResult
from .base import PluginContext, PluginManifest


class FileToolsPlugin:
    """Runtime 内置文件工具适配器；真正的路径边界由 task FileWorkspace 执行。"""

    manifest = PluginManifest(
        plugin_id="files",
        version="1.0.0",
        description="只访问当前 task 私有工作区的文本 list/read/search/write/edit 工具",
        capabilities=("task-workspace:read", "task-workspace:write"),
    )

    _schemas: tuple[dict[str, Any], ...] = (
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "递归列出当前 task 工作区内最多 100 个文本文件。",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "default": "."}},
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取当前 task 工作区中的 UTF-8 文本和 SHA256。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "offset": {"type": "integer", "default": 0},
                        "limit": {"type": "integer", "default": 16384},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": "在当前 task 工作区做有界字面量搜索，不执行正则。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string", "default": "."},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "新建文本；覆盖时必须提交当前 expectedSha256。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "expectedSha256": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "在 SHA256 匹配时唯一替换 oldText，并返回有界 diff。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "oldText": {"type": "string"},
                        "newText": {"type": "string"},
                        "expectedSha256": {"type": "string"},
                    },
                    "required": ["path", "oldText", "newText", "expectedSha256"],
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
        workspace = context.workspace
        if tool_name == "list_files":
            body = workspace.list_files(str(arguments.get("path", ".")))
        elif tool_name == "read_file":
            body = workspace.read_file(
                str(arguments["path"]),
                int(arguments.get("offset", 0)),
                int(arguments.get("limit", 16384)),
            )
        elif tool_name == "search_files":
            body = workspace.search_files(
                str(arguments["query"]),
                str(arguments.get("path", ".")),
            )
        elif tool_name == "write_file":
            body = workspace.write_file(
                str(arguments["path"]),
                str(arguments["content"]),
                arguments.get("expectedSha256"),
            )
        elif tool_name == "edit_file":
            body = workspace.edit_file(
                str(arguments["path"]),
                str(arguments["oldText"]),
                str(arguments["newText"]),
                str(arguments["expectedSha256"]),
            )
        else:
            raise ValueError("文件工具插件不支持工具: %s" % tool_name)
        return HTTPResult(200, body)
