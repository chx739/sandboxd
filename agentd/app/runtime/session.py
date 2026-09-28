from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
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
from ..redaction import public_error, safe_tool_arguments

_SESSION_ID = re.compile(r"^session-[a-f0-9]{16}$")
_NODE_ID = re.compile(r"^node-[a-f0-9]{16}$")
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
        safe_calls = []
        for call in message.tool_calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name", ""))
            arguments = call.get("args", {})
            safe_call = dict(call)
            if isinstance(arguments, dict):
                safe_call["args"] = safe_tool_arguments(name, arguments)
            safe_calls.append(safe_call)
        payload["toolCalls"] = _safe_json_value(safe_calls)
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


def _branchable_flags(messages: Sequence[dict[str, Any]]) -> list[bool]:
    """只有完整的模型 Turn 才可作为分支点，避免恢复后重放半个工具调用。"""

    pending: set[str] = set()
    valid = True
    flags: list[bool] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            if pending:
                valid = False
            calls = message.get("toolCalls", [])
            if not isinstance(calls, list):
                valid = False
                calls = []
            ids = [str(call.get("id", "")) for call in calls if isinstance(call, dict)]
            if any(not call_id for call_id in ids) or len(ids) != len(calls):
                valid = False
            pending = set(ids)
            if len(pending) != len(ids):
                valid = False
        elif role == "tool":
            call_id = str(message.get("toolCallId", ""))
            if call_id not in pending:
                valid = False
            else:
                pending.remove(call_id)
        elif role not in {"system", "user"} or pending:
            valid = False
        flags.append(valid and not pending and role in {"assistant", "tool"})
    return flags


def _path_nodes(nodes: dict[str, dict[str, Any]], leaf_id: str) -> list[dict[str, Any]]:
    path: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = leaf_id
    while current is not None:
        if current in seen or current not in nodes:
            raise ValueError("Session 节点父链损坏")
        seen.add(current)
        node = nodes[current]
        path.append(node)
        parent = node.get("parentId")
        current = str(parent) if parent is not None else None
    path.reverse()
    return path


class SessionJournal:
    """追加式 Session 树；旧的线性 transcript 在首次读取树时自动迁移。"""

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
        parent_node_id: str | None = None,
    ) -> None:
        if self._path.exists():
            if parent_node_id is not None and not _NODE_ID.fullmatch(parent_node_id):
                raise ValueError("非法 Session 父节点")
            events = [("run.started", {"taskId": task_id, "parentNodeId": parent_node_id})]
            if parent_node_id is not None:
                # 分支一旦选中，活动叶子先退回安全的完整 Turn 边界。
                events.append(("session.head", {"taskId": task_id, "nodeId": parent_node_id}))
            await self._append_many(events)
            return
        await self._append(
            "session.header",
            {
                "sessionId": self.session_id,
                "taskId": task_id,
                # 告警注解同样是不可信外部文本，可能夹带 Header 或 API Key。
                "alert": _safe_json_value(
                    alert.model_dump(mode="json", by_alias=True)
                ),
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
        serialized = [_serialize_message(message) for message in messages]
        entries = await self._read_entries()
        nodes = {
            str(entry["nodeId"]): entry
            for entry in entries
            if entry.get("type") == "session.node" and isinstance(entry.get("nodeId"), str)
        }
        runs = [
            entry for entry in entries
            if entry.get("type") == "run.started" and entry.get("taskId") == task_id
        ]
        parent_id = runs[-1].get("parentNodeId") if runs else None
        base = _path_nodes(nodes, str(parent_id)) if parent_id is not None else []
        base_messages = [node["message"] for node in base]
        if serialized[: len(base_messages)] != base_messages:
            raise ValueError("恢复消息与选定 Session 分支不一致")

        flags = _branchable_flags(serialized)
        events: list[tuple[str, dict[str, Any]]] = []
        leaf_id = str(parent_id) if parent_id is not None else None
        safe_head = leaf_id
        for index, payload in enumerate(serialized[len(base_messages):], len(base_messages)):
            node_id = "node-" + secrets.token_hex(8)
            events.append(("session.node", {
                "taskId": task_id,
                "nodeId": node_id,
                "parentId": leaf_id,
                "message": payload,
                "branchable": flags[index],
            }))
            leaf_id = node_id
            if flags[index]:
                safe_head = node_id
        if safe_head is not None:
            events.append(("session.head", {"taskId": task_id, "nodeId": safe_head}))
        # 旧审计工具仍可读快照；新的恢复依据始终是节点父链。
        events.append(("session.transcript", {"taskId": task_id, "messages": serialized}))
        await self._append_many(events)

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
        await self._ensure_tree()
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
        nodes = self._nodes(entries)
        head_id = self._head_id(entries, nodes)
        path_count = len(_path_nodes(nodes, head_id)) if head_id else 0
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
            "messageCount": path_count or len(last_transcript.get("messages", [])),
            "nodeCount": len(nodes),
            "activeLeafId": head_id,
            "commandCount": len(commands),
            "runCount": 1
            + sum(entry.get("type") == "run.started" for entry in entries),
            "status": status,
            "updatedAt": entries[-1].get("timestamp", ""),
        }

    async def load_for_resume(
        self,
    ) -> tuple[AlertEvent, list[BaseMessage]]:
        alert, messages, _ = await self.load_branch()
        return alert, messages

    async def load_branch(
        self,
        node_id: str | None = None,
    ) -> tuple[AlertEvent, list[BaseMessage], str]:
        await self._ensure_tree()
        entries = await self._read_entries()
        header = next(
            (entry for entry in entries if entry.get("type") == "session.header"),
            None,
        )
        if header is None:
            raise ValueError("Session 缺少 Header")
        nodes = self._nodes(entries)
        selected = node_id or self._head_id(entries, nodes)
        if selected is None or selected not in nodes:
            raise ValueError("Session 尚无可恢复的完整 Turn")
        if not nodes[selected].get("branchable"):
            raise ValueError("只能从完整 Turn 的末尾分支或恢复")
        alert = AlertEvent.model_validate(header.get("alert", {}))
        raw_messages = [node["message"] for node in _path_nodes(nodes, selected)]
        messages = [
            _deserialize_message(item)
            for item in raw_messages
            if isinstance(item, dict)
        ]
        if not messages:
            raise ValueError("Session Transcript 为空")
        return alert, messages, selected

    async def tree(self) -> dict[str, Any]:
        await self._ensure_tree()
        entries = await self._read_entries()
        nodes = self._nodes(entries)
        return {
            "sessionId": self.session_id,
            "activeLeafId": self._head_id(entries, nodes),
            "nodes": [
                {
                    "nodeId": node["nodeId"],
                    "parentId": node.get("parentId"),
                    "taskId": node.get("taskId"),
                    "role": node.get("message", {}).get("role"),
                    "branchable": bool(node.get("branchable")),
                }
                for node in nodes.values()
            ],
        }

    async def path_messages(self, node_id: str) -> dict[str, Any]:
        await self._ensure_tree()
        entries = await self._read_entries()
        nodes = self._nodes(entries)
        if node_id not in nodes:
            raise ValueError("Session 节点不存在")
        return {
            "sessionId": self.session_id,
            "nodeId": node_id,
            "branchable": bool(nodes[node_id].get("branchable")),
            "messages": [node["message"] for node in _path_nodes(nodes, node_id)],
        }

    async def active_path(self) -> list[dict[str, Any]]:
        """供记忆提取使用；返回带来源节点 ID 的当前完整路径。"""

        await self._ensure_tree()
        entries = await self._read_entries()
        nodes = self._nodes(entries)
        head_id = self._head_id(entries, nodes)
        if head_id is None:
            raise ValueError("Session 尚无完整 Turn")
        return _path_nodes(nodes, head_id)

    @staticmethod
    def _nodes(entries: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            str(entry["nodeId"]): entry
            for entry in entries
            if entry.get("type") == "session.node" and isinstance(entry.get("nodeId"), str)
        }

    @staticmethod
    def _head_id(
        entries: Sequence[dict[str, Any]],
        nodes: dict[str, dict[str, Any]],
    ) -> str | None:
        for entry in reversed(entries):
            if entry.get("type") == "session.head":
                node_id = str(entry.get("nodeId", ""))
                if node_id in nodes and nodes[node_id].get("branchable"):
                    return node_id
        return None

    async def _ensure_tree(self) -> None:
        """旧文件只迁移最后一份完整快照；尾部半个工具调用不成为分支。"""

        async with self._lock:
            entries = self._read_entries_unlocked()
            if any(entry.get("type") == "session.node" for entry in entries):
                return
            transcripts = [
                entry for entry in entries if entry.get("type") == "session.transcript"
            ]
            if not transcripts:
                return
            messages = transcripts[-1].get("messages")
            if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
                raise ValueError("旧 Session Transcript 格式错误")
            flags = _branchable_flags(messages)
            safe = [index for index, flag in enumerate(flags) if flag]
            if not safe:
                return
            parent_id: str | None = None
            events: list[tuple[str, dict[str, Any]]] = []
            for index, message in enumerate(messages[: safe[-1] + 1]):
                node_id = "node-" + secrets.token_hex(8)
                events.append(("session.node", {
                    "taskId": transcripts[-1].get("taskId", "legacy"),
                    "nodeId": node_id,
                    "parentId": parent_id,
                    "message": message,
                    "branchable": flags[index],
                }))
                parent_id = node_id
            events.append(("session.head", {"taskId": "legacy-migration", "nodeId": parent_id}))
            self._write_events_unlocked(events)

    async def _append(self, entry_type: str, values: dict[str, Any]) -> None:
        await self._append_many([(entry_type, values)])

    async def _append_many(
        self,
        events: Sequence[tuple[str, dict[str, Any]]],
    ) -> None:
        async with self._lock:
            self._write_events_unlocked(events)

    def _write_events_unlocked(
        self,
        events: Sequence[tuple[str, dict[str, Any]]],
    ) -> None:
        if not events:
            return
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._directory.chmod(0o700)
        with self._path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    # 崩溃留下的末行不是事件；先截掉，否则新事件会接在坏行后。
                    handle.seek(0)
                    last_newline = handle.read().rfind(b"\n")
                    handle.truncate(last_newline + 1)
            handle.seek(0, os.SEEK_END)
            for entry_type, values in events:
                entry = {"type": entry_type, "timestamp": _now(), **values}
                line = json.dumps(
                    entry,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
                handle.write((line + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        self._path.chmod(0o600)

    async def _read_entries(self) -> list[dict[str, Any]]:
        async with self._lock:
            return self._read_entries_unlocked()

    def _read_entries_unlocked(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            raise FileNotFoundError(self.session_id)
        entries: list[dict[str, Any]] = []
        lines = self._path.read_bytes().splitlines(keepends=True)
        for index, raw in enumerate(lines):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                if index == len(lines) - 1 and not raw.endswith(b"\n"):
                    break
                raise ValueError("Session JSONL 包含损坏的完整行") from None
            if not isinstance(value, dict):
                raise ValueError("Session JSONL 行必须是 object")
            entries.append(value)
        return entries
