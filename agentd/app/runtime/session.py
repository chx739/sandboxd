from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from ..models import AlertEvent
from ..redaction import public_error

_SESSION_ID = re.compile(r"^session-[a-f0-9]{16}$")
_MAX_MESSAGE_CHARS = 16 << 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_content(value: object) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return public_error(text, limit=_MAX_MESSAGE_CHARS)


def _safe_json_value(value: object, depth: int = 0) -> Any:
    """递归脱敏 Tool 参数，同时保持 JSON 结构可恢复。

    不能先把整个 JSON 字符串交给正则再 ``json.loads``：凭据替换可能改变引号，
    而长度截断也会产生不完整 JSON。逐个叶子处理可以同时保证脱敏和格式合法。
    """

    if depth >= 8:
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, str):
        return public_error(value, limit=2048)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        return [_safe_json_value(item, depth + 1) for item in value[:32]]
    if isinstance(value, dict):
        return {
            public_error(str(key), limit=128): _safe_json_value(item, depth + 1)
            for key, item in list(value.items())[:64]
        }
    return public_error(str(value), limit=2048)


def _serialize_message(message: BaseMessage) -> dict[str, Any]:
    """只保存 Provider 无关的公开消息字段，不保存 additional_kwargs/隐藏 CoT。"""

    if isinstance(message, SystemMessage):
        role = "system"
    elif isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, AIMessage):
        role = "assistant"
    elif isinstance(message, ToolMessage):
        role = "tool"
    else:
        raise TypeError("Session 不支持消息类型: %s" % type(message).__name__)

    payload: dict[str, Any] = {
        "role": role,
        "content": _safe_content(message.content),
    }
    if isinstance(message, AIMessage):
        # Tool 参数逐叶脱敏；不保存 additional_kwargs 和模型隐藏思维。
        payload["toolCalls"] = _safe_json_value(message.tool_calls)
    if isinstance(message, ToolMessage):
        payload["toolCallId"] = str(message.tool_call_id)
    return payload


def _deserialize_message(payload: dict[str, Any]) -> BaseMessage:
    role = payload.get("role")
    content = str(payload.get("content", ""))
    if role == "system":
        return SystemMessage(content=content)
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        calls = payload.get("toolCalls", [])
        return AIMessage(
            content=content,
            tool_calls=calls if isinstance(calls, list) else [],
        )
    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=str(payload.get("toolCallId", "")),
        )
    raise ValueError("Session 包含未知消息 role: %s" % role)


class SessionJournal:
    """线性 append-only Session 文件。

    JSONL 每行都是独立事件，进程中途退出时最多损失最后一行；恢复只读取最后一个
    完整 transcript。它不是数据库，也不支持树形分支，正好对应秋招 Demo 的范围。
    """

    def __init__(self, directory: Path, session_id: str) -> None:
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError("非法 session id")
        self.session_id = session_id
        self._directory = directory
        self._path = directory / (session_id + ".jsonl")
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def initialize(
        self,
        task_id: str,
        alert: AlertEvent,
    ) -> None:
        if self._path.exists():
            await self._append("run.started", {"taskId": task_id})
            return
        await self._append(
            "session.header",
            {
                "sessionId": self.session_id,
                "taskId": task_id,
                "alert": alert.model_dump(mode="json", by_alias=True),
            },
        )

    async def append_command(
        self,
        task_id: str,
        command: str,
        content: str = "",
    ) -> None:
        await self._append(
            "session.command",
            {
                "taskId": task_id,
                "command": command,
                "content": public_error(content, limit=4096),
            },
        )

    async def append_transcript(
        self,
        task_id: str,
        messages: Sequence[BaseMessage],
    ) -> None:
        await self._append(
            "session.transcript",
            {
                "taskId": task_id,
                "messages": [_serialize_message(message) for message in messages],
            },
        )

    async def append_result(
        self,
        task_id: str,
        status: str,
        summary: str = "",
    ) -> None:
        await self._append(
            "session.result",
            {
                "taskId": task_id,
                "status": status,
                "summary": public_error(summary, limit=2000),
            },
        )

    async def summary(self) -> dict[str, Any]:
        entries = await self._read_entries()
        if not entries:
            raise FileNotFoundError(self.session_id)

        header = next(
            (entry for entry in entries if entry.get("type") == "session.header"),
            None,
        )
        transcripts = [
            entry for entry in entries if entry.get("type") == "session.transcript"
        ]
        commands = [
            entry for entry in entries if entry.get("type") == "session.command"
        ]
        last_transcript = transcripts[-1] if transcripts else {}
        current_task_id = str((header or {}).get("taskId", ""))
        status = "running"
        # 不能简单取最后一个 result：resume 会先追加 run.started，此时旧 run 的
        # succeeded/cancelled 已经过期，Session 当前状态应重新变成 running。
        for entry in entries:
            if entry.get("type") in {"session.header", "run.started"}:
                current_task_id = str(entry.get("taskId", current_task_id))
                status = "running"
            elif entry.get("type") == "session.result":
                status = str(entry.get("status", status))
        return {
            "sessionId": self.session_id,
            "taskId": current_task_id,
            "messageCount": len(last_transcript.get("messages", [])),
            "commandCount": len(commands),
            "runCount": 1
            + sum(entry.get("type") == "run.started" for entry in entries),
            "status": status,
            "updatedAt": entries[-1].get("timestamp", ""),
        }

    async def load_for_resume(
        self,
    ) -> tuple[AlertEvent, list[BaseMessage]]:
        entries = await self._read_entries()
        header = next(
            (entry for entry in entries if entry.get("type") == "session.header"),
            None,
        )
        transcripts = [
            entry for entry in entries if entry.get("type") == "session.transcript"
        ]
        if header is None or not transcripts:
            raise ValueError("Session 尚无可恢复的完整 Transcript")

        alert = AlertEvent.model_validate(header.get("alert", {}))
        raw_messages = transcripts[-1].get("messages", [])
        if not isinstance(raw_messages, list):
            raise ValueError("Session Transcript 格式错误")
        messages = [
            _deserialize_message(item)
            for item in raw_messages
            if isinstance(item, dict)
        ]
        if not messages:
            raise ValueError("Session Transcript 为空")
        return alert, messages

    async def _append(self, entry_type: str, values: dict[str, Any]) -> None:
        entry = {
            "type": entry_type,
            "timestamp": _now(),
            **values,
        }
        line = json.dumps(
            entry,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        async with self._lock:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._directory.chmod(0o700)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._path.chmod(0o600)

    async def _read_entries(self) -> list[dict[str, Any]]:
        async with self._lock:
            if not self._path.exists():
                raise FileNotFoundError(self.session_id)
            entries: list[dict[str, Any]] = []
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("Session JSONL 行必须是 object")
                entries.append(value)
            return entries
