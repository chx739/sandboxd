"""业务文件的远程适配器；不创建本地目录，也不重试结果未知的写入。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from ..clients import HTTPResult, SandboxdClient


class WorkspaceExecutionError(RuntimeError):
    def __init__(self, message: str, *, recoverable: bool, outcome_uncertain: bool = False) -> None:
        super().__init__(message)
        self.recoverable = recoverable
        self.outcome_uncertain = outcome_uncertain


@dataclass(frozen=True)
class WorkspaceBinding:
    workspace_id: str
    task_id: str
    attempt_id: str
    sandbox_id: str
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, str) or not re.fullmatch(r"workspace-[a-f0-9]{32}", self.workspace_id):
            raise ValueError("invalid workspace identity")
        for value, prefix in ((self.task_id, "task-"), (self.attempt_id, "attempt-"), (self.sandbox_id, "")):
            if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", value) or not value.startswith(prefix):
                raise ValueError("invalid workspace owner")
        if type(self.generation) is not int or not 1 <= self.generation < 1 << 64:
            raise ValueError("invalid workspace generation")

    def as_request(self) -> dict[str, Any]:
        return {"workspaceId": self.workspace_id, "taskId": self.task_id,
                "attemptId": self.attempt_id, "sandboxId": self.sandbox_id,
                "generation": self.generation}

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> WorkspaceBinding:
        owner = record["owner"]
        return cls(record["workspaceId"], owner["taskId"], owner["attemptId"],
                   owner["sandboxId"], record["generation"])


class RemoteFileWorkspace:
    """绑定由可信 Runner 给定，模型参数不能替换工作区或 attempt。"""

    def __init__(self, client: SandboxdClient, binding: WorkspaceBinding) -> None:
        self.client = client
        self.binding = binding

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> HTTPResult:
        if tool_name not in {"list_files", "read_file", "search_files", "write_file", "edit_file"} or "operation" in arguments:
            raise ValueError("unsupported remote file operation")
        operation = dict(arguments)
        operation["operation"] = tool_name
        if tool_name in {"list_files", "search_files"}:
            operation.setdefault("path", ".")
        writing = tool_name in {"write_file", "edit_file"}
        try:
            result = await self.client.workspace_files(self.binding.as_request(), operation)
        except (httpx.HTTPError, ValueError) as exc:
            # 不把响应丢失解释为“写入没发生”；本层没有自动重试。
            raise WorkspaceExecutionError("workspace transport failed", recoverable=True,
                                          outcome_uncertain=writing) from exc
        if result.status_code in {401, 404, 409}:
            raise WorkspaceExecutionError("workspace identity is not authorized", recoverable=False)
        if result.status_code >= 500 or not isinstance(result.body, dict):
            recoverable = isinstance(result.body, dict) and result.body.get("sandboxRecovery") is True
            raise WorkspaceExecutionError("workspace execution unavailable", recoverable=recoverable,
                                          outcome_uncertain=writing)
        return result
