"""为每次调用构造有界模型视图；Session 原始消息保持可回放。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from .working_memory import state_freshness

MAX_MODEL_CONTEXT_CHARS = 48 << 10
MAX_ESTIMATED_CONTEXT_TOKENS = 32768
OUTPUT_TOKEN_RESERVE = 4096


@dataclass(frozen=True)
class ContextTransform:
    messages: list[BaseMessage]
    before_chars: int
    after_chars: int
    before_messages: int
    after_messages: int
    estimated_tokens: int = 0
    tool_schema_estimated_tokens: int = 0
    compacted_groups: int = 0

    @property
    def trimmed(self) -> bool:
        return self.compacted_groups > 0 or self.after_messages < self.before_messages


def _message_chars(message: BaseMessage) -> int:
    content = message.content
    if isinstance(content, str):
        size = len(content)
    else:
        size = len(json.dumps(content, ensure_ascii=False, default=str))
    if isinstance(message, AIMessage) and message.tool_calls:
        size += len(json.dumps(message.tool_calls, ensure_ascii=False, default=str))
    return size


def _estimate_tokens(messages: Sequence[BaseMessage]) -> int:
    # UTF-8 字节加协议开销是跨模型的保守估算，不冒充 Provider tokenizer。
    return sum(len(str(message.content).encode("utf-8")) + 16 for message in messages) + sum(
        len(json.dumps(message.tool_calls, ensure_ascii=False, default=str).encode("utf-8"))
        for message in messages if isinstance(message, AIMessage) and message.tool_calls
    )


def _runtime_memory(message: BaseMessage) -> bool:
    if not isinstance(message, HumanMessage) or not isinstance(message.content, str):
        return False
    try:
        value = json.loads(message.content)
    except ValueError:
        return False
    return isinstance(value, dict) and value.get("kind") in {
        "historical-memory", "task-working-memory",
    }


def _has_denial(group: Sequence[BaseMessage]) -> bool:
    for message in group:
        if not isinstance(message, ToolMessage):
            continue
        try:
            value = json.loads(str(message.content))
        except ValueError:
            continue
        if isinstance(value, dict) and (value.get("denied") is True or value.get("denyLayer")):
            return True
    return False


def _compact_group(group: Sequence[BaseMessage]) -> list[BaseMessage]:
    """缩短模型视图里的结果正文，保留每个 ToolMessage 的协议 ID。"""
    result: list[BaseMessage] = []
    for message in group:
        if not isinstance(message, ToolMessage) or _message_chars(message) <= 384:
            result.append(message)
            continue
        try:
            value = json.loads(str(message.content))
        except ValueError:
            value = {}
        if not isinstance(value, dict):
            value = {}
        compact = {
            "ok": value.get("ok"),
            "denied": value.get("denied", False),
            "denyLayer": value.get("denyLayer"),
            "evidenceId": value.get("evidenceId"),
            "observedAt": value.get("observedAt"),
            "truncatedForContext": True,
            "excerpt": str(message.content)[:128],
        }
        result.append(ToolMessage(
            content=json.dumps(compact, ensure_ascii=False),
            tool_call_id=message.tool_call_id,
        ))
    return result


def _mark_stale_view(message: BaseMessage, stale_ids: set[str]) -> BaseMessage:
    if not isinstance(message, ToolMessage):
        return message
    try:
        value = json.loads(str(message.content))
    except ValueError:
        return message
    if not isinstance(value, dict) or value.get("evidenceId") not in stale_ids:
        return message
    historical = {
        "ok": value.get("ok"),
        "denied": value.get("denied", False),
        "denyLayer": value.get("denyLayer"),
        "error": str(value.get("error", ""))[:180],
        "evidenceId": value.get("evidenceId"),
        "observedAt": value.get("observedAt"),
        "freshness": "stale",
        "requiresRecheckForCurrentClaim": True,
        "historicalExcerpt": str(value.get("body", ""))[:180],
    }
    return ToolMessage(content=json.dumps(historical, ensure_ascii=False),
                       tool_call_id=message.tool_call_id)


def _stale_state_ids(
    messages: Sequence[BaseMessage], working_memory: dict[str, Any] | None,
) -> set[str]:
    stale = {
        item.get("evidenceId") for item in (working_memory or {}).get("observations", [])
        if isinstance(item, dict) and item.get("category") == "current_state"
        and item.get("freshness") != "current"
    }
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    now = datetime.now(timezone.utc)
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if isinstance(call, dict):
                    args = call.get("args", {})
                    calls[str(call.get("id", ""))] = (
                        str(call.get("name", "")), args if isinstance(args, dict) else {},
                    )
        elif isinstance(message, ToolMessage):
            name, args = calls.get(str(message.tool_call_id), ("", {}))
            if name not in {"kubernetes_read", "query_prometheus", "linux_read"} or (
                name == "kubernetes_read" and args.get("operation") in {
                    "get_pod_logs", "list_events",
                }
            ):
                continue
            payload: Any = None
            try:
                payload = json.loads(str(message.content))
                if isinstance(payload, dict) and state_freshness(name, payload, now) != "current":
                    stale.add(payload.get("evidenceId"))
            except (ValueError, TypeError, KeyError):
                if isinstance(payload, dict):
                    stale.add(payload.get("evidenceId"))
                continue
    return {ref for ref in stale if isinstance(ref, str)}


def transform_model_context(
    messages: Sequence[BaseMessage],
    budget: int = MAX_MODEL_CONTEXT_CHARS,
    *,
    working_memory: dict[str, Any] | None = None,
    memory_summary: str = "",
    tool_schemas: Sequence[dict[str, Any]] = (),
    token_budget: int = MAX_ESTIMATED_CONTEXT_TOKENS,
    output_reserve: int = OUTPUT_TOKEN_RESERVE,
) -> ContextTransform:
    source = list(messages)
    if not source or not isinstance(source[0], SystemMessage):
        raise ValueError("模型上下文第一条必须是安全 SystemMessage")
    if len(source) < 2:
        raise ValueError("模型上下文缺少初始 Alert 消息")
    if budget <= 0 or token_budget <= output_reserve:
        raise ValueError("上下文预算不合法")

    before_chars = sum(_message_chars(message) for message in source)
    stale_ids = _stale_state_ids(source, working_memory)
    source = source[:2] + [
        _mark_stale_view(message, stale_ids) for message in source[2:]
        if not _runtime_memory(message)
    ]
    prefix = source[:2]
    if working_memory and any(working_memory.get(field) for field in (
        "observations", "citedEvidence", "hypotheses", "completedChecks", "pendingChecks",
    )):
        prefix.append(HumanMessage(content=json.dumps({
            "kind": "task-working-memory", "trustLevel": "untrusted-data",
            "content": working_memory,
        }, ensure_ascii=False)))
    if memory_summary:
        prefix.append(HumanMessage(content=json.dumps({
            "kind": "historical-memory", "trustLevel": "untrusted-data",
            "content": memory_summary,
        }, ensure_ascii=False)))

    groups: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []
    for message in source[2:]:
        if isinstance(message, AIMessage) and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)

    schemas_size = len(json.dumps(tool_schemas, ensure_ascii=False, default=str).encode("utf-8"))
    input_limit = token_budget - output_reserve - schemas_size
    if input_limit <= 0:
        raise ValueError("工具定义已耗尽上下文预算")

    def fits(candidate: Sequence[BaseMessage]) -> bool:
        return sum(_message_chars(item) for item in candidate) <= budget and _estimate_tokens(candidate) <= input_limit

    if not fits(prefix):
        raise ValueError("固定规则、告警与记忆超过上下文预算")

    selected: dict[int, list[BaseMessage]] = {}
    compacted = 0
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        candidate = prefix + [item for i in sorted(selected) for item in selected[i]] + group
        if fits(candidate):
            selected[index] = group
            continue
        if not selected:
            shorter = _compact_group(group)
            if not fits(prefix + shorter):
                raise ValueError("最新完整工具轮次超过上下文预算")
            selected[index] = shorter
            compacted += 1
            continue
        break

    # 最近一次拒绝是安全诊断依据；必要时用短视图代替较旧的普通轮次。
    denied_indexes = [i for i, group in enumerate(groups) if _has_denial(group)]
    if denied_indexes and denied_indexes[-1] not in selected:
        index = denied_indexes[-1]
        denied_group = _compact_group(groups[index])
        newest = max(selected)
        while len(selected) > 1 and not fits(prefix + denied_group + [
            item for i in sorted(selected) for item in selected[i]
        ]):
            selected.pop(min(selected))
        if not fits(prefix + denied_group + [
            item for i in sorted(selected) for item in selected[i]
        ]):
            raise ValueError("拒绝证据与最新交互超过上下文预算")
        assert newest in selected
        selected[index] = denied_group
        compacted += 1

    kept = prefix + [item for i in sorted(selected) for item in selected[i]]
    return ContextTransform(
        kept,
        before_chars,
        sum(_message_chars(item) for item in kept),
        len(messages),
        len(kept),
        _estimate_tokens(kept) + schemas_size + output_reserve,
        schemas_size,
        compacted + len(groups) - len(selected),
    )
