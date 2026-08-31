from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

MAX_MODEL_CONTEXT_CHARS = 48 << 10


@dataclass(frozen=True)
class ContextTransform:
    messages: list[BaseMessage]
    before_chars: int
    after_chars: int
    before_messages: int
    after_messages: int

    @property
    def trimmed(self) -> bool:
        return self.after_messages < self.before_messages


def _message_chars(message: BaseMessage) -> int:
    content = message.content
    if isinstance(content, str):
        size = len(content)
    else:
        size = len(json.dumps(content, ensure_ascii=False, default=str))
    if isinstance(message, AIMessage) and message.tool_calls:
        size += len(json.dumps(message.tool_calls, ensure_ascii=False, default=str))
    return size


def transform_model_context(
    messages: Sequence[BaseMessage],
    budget: int = MAX_MODEL_CONTEXT_CHARS,
) -> ContextTransform:
    """保留安全提示和完整工具协议，只从最旧的已完成轮次开始裁剪。"""
    source = list(messages)
    if not source or not isinstance(source[0], SystemMessage):
        raise ValueError("模型上下文第一条必须是安全 SystemMessage")
    if len(source) < 2:
        raise ValueError("模型上下文缺少初始 Alert 消息")

    before_chars = sum(_message_chars(message) for message in source)
    if before_chars <= budget:
        return ContextTransform(
            source,
            before_chars,
            before_chars,
            len(source),
            len(source),
        )

    fixed = source[:2]
    groups: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []
    for message in source[2:]:
        # Assistant Tool Call 和它后面的 ToolResult 必须作为一个整体保留。
        if isinstance(message, AIMessage) and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)

    selected: list[list[BaseMessage]] = []
    used = sum(_message_chars(message) for message in fixed)
    for group in reversed(groups):
        group_size = sum(_message_chars(message) for message in group)
        if used + group_size <= budget or not selected:
            selected.append(group)
            used += group_size
        else:
            break

    kept = fixed + [
        message
        for group in reversed(selected)
        for message in group
    ]
    return ContextTransform(
        kept,
        before_chars,
        sum(_message_chars(message) for message in kept),
        len(source),
        len(kept),
    )
