from __future__ import annotations

import json
import asyncio
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_openai import ChatOpenAI

from .models import ModelUsage

@dataclass(frozen=True)
class ModelInvocation:
    message: AIMessage
    usage: ModelUsage
    finish_reason: str
    elapsed_ms: int
    usage_known: bool = False


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


class ModelSession(Protocol):
    async def invoke(self, messages: Sequence[BaseMessage]) -> ModelInvocation: ...


class ModelGateway(Protocol):
    mode: str
    model_name: str
    provider_name: str
    capabilities: dict[str, Any]

    def new_session(self, tool_schemas: Sequence[dict[str, Any]]) -> ModelSession: ...


class LiveModelSession:
    def __init__(self, model: ChatOpenAI, tool_schemas: Sequence[dict[str, Any]]) -> None:
        # Tool Schema 来自受信任 Plugin Registry。Provider 只负责绑定工具，
        # 不决定哪些插件可加载，也不承担安全授权。
        self._bound_model = model.bind_tools(list(tool_schemas)) if tool_schemas else model

    async def invoke(self, messages: Sequence[BaseMessage]) -> ModelInvocation:
        started = time.monotonic()
        response = await self._bound_model.ainvoke(list(messages))
        if not isinstance(response, AIMessage):
            raise TypeError("模型没有返回 AIMessage")
        raw_usage = response.usage_metadata or {}
        usage = ModelUsage(
            inputTokens=_non_negative_int(raw_usage.get("input_tokens")),
            outputTokens=_non_negative_int(raw_usage.get("output_tokens")),
            totalTokens=_non_negative_int(raw_usage.get("total_tokens")),
        )
        return ModelInvocation(
            message=response,
            usage=usage,
            finish_reason=str(response.response_metadata.get("finish_reason") or "unknown"),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            usage_known=all(type(raw_usage.get(key)) is int and raw_usage[key] >= 0
                            for key in ("input_tokens", "output_tokens", "total_tokens")),
        )


class LiveModelGateway:
    mode = "live"
    provider_name = "openai-compatible"
    capabilities = {"toolCalling": True, "usageMetadata": True}

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        thinking: str = "default",
        max_tokens: int | None = None,
        max_retries: int = 1,
    ) -> None:
        self.model_name = model
        self.automatic_retries = max_retries
        self.recovery_fingerprint = hashlib.sha256(json.dumps({
            "baseUrl": base_url.rstrip("/"), "model": model, "thinking": thinking,
            "maxTokens": max_tokens, "maxRetries": max_retries,
        }, sort_keys=True).encode()).hexdigest()
        if max_tokens is not None and max_tokens <= 0:
            raise ValueError("max_tokens 必须为正数")
        if max_retries < 0:
            raise ValueError("max_retries 不能为负数")
        provider_options: dict[str, Any] = {}
        if thinking != "default":
            # DeepSeek V4 默认返回 reasoning_content；关闭后无需保存或回传隐藏 CoT。
            provider_options["extra_body"] = {"thinking": {"type": thinking}}
        if max_tokens is not None:
            provider_options["max_tokens"] = max_tokens
        self._model = ChatOpenAI(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=0,
            timeout=30,
            max_retries=max_retries,
            **provider_options,
        )

    def new_session(self, tool_schemas: Sequence[dict[str, Any]]) -> ModelSession:
        return LiveModelSession(self._model, tool_schemas)


class ReplayModelSession:
    def __init__(self, responses: list[dict[str, Any]], cursor: int = 0) -> None:
        if type(cursor) is not int or not 0 <= cursor <= len(responses):
            raise ValueError("invalid persisted Replay cursor")
        self._responses = responses
        self._index = cursor

    async def invoke(self, messages: Sequence[BaseMessage]) -> ModelInvocation:
        if self._index >= len(self._responses):
            raise RuntimeError("Replay 响应已耗尽但 Graph 仍在请求模型")
        item = self._responses[self._index]
        self._index += 1
        delay_ms = item.get("delayMs", 0)
        if type(delay_ms) is not int or not 0 <= delay_ms <= 60_000:
            raise ValueError("Replay delayMs must be an integer in [0,60000]")
        started = time.monotonic()
        if delay_ms:
            # 仅固定Replay夹具模拟在途响应，便于真实进程中断；不影响Live Provider。
            await asyncio.sleep(delay_ms / 1000)
        tool_calls = [
            {
                "id": str(call["id"]),
                "name": str(call["name"]),
                "args": _resolve_replay_value(dict(call.get("args", {})), messages),
                "type": "tool_call",
            }
            for call in item.get("toolCalls", [])
        ]
        return ModelInvocation(
            message=AIMessage(content=str(item.get("content", "")), tool_calls=tool_calls),
            usage=ModelUsage(),
            finish_reason="tool_calls" if tool_calls else "stop",
            elapsed_ms=int((time.monotonic() - started) * 1000) if delay_ms else 0,
            usage_known=True,  # Replay明确没有外部模型token用量，不冒称真实Provider计费。
        )


def _resolve_replay_value(
    value: Any,
    messages: Sequence[BaseMessage],
) -> Any:
    # Replay 只支持这个明确占位符；真实模型不会经过本逻辑。
    if isinstance(value, str) and value == "{{first_pod_name}}":
        return _first_pod_name(messages)
    if isinstance(value, dict):
        return {
            key: _resolve_replay_value(item, messages)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_replay_value(item, messages) for item in value]
    return value


def _first_pod_name(messages: Sequence[BaseMessage]) -> str:
    for message in reversed(messages):
        if (
            not isinstance(message, ToolMessage)
            or not isinstance(message.content, str)
        ):
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        body = payload.get("body") if isinstance(payload, dict) else None
        documents = [body] if isinstance(body, dict) else []
        if isinstance(body, dict) and isinstance(body.get("stdout"), str):
            try:
                stdout = json.loads(body["stdout"])
            except json.JSONDecodeError:
                stdout = None
            if isinstance(stdout, dict):
                documents.append(stdout)
        for document in documents:
            items = document.get("items")
            if not isinstance(items, list) or not items:
                continue
            metadata = (
                items[0].get("metadata")
                if isinstance(items[0], dict) else None
            )
            name = metadata.get("name") if isinstance(metadata, dict) else None
            if isinstance(name, str) and name:
                return name
    raise RuntimeError("Replay 无法从已验证的 list_pods Observation 解析 Pod 名")


class ReplayModelGateway:
    mode = "replay"
    provider_name = "replay"
    capabilities = {"toolCalling": True, "deterministic": True}

    def __init__(self, path: Path) -> None:
        raw = path.read_bytes()
        payload = json.loads(raw)
        self.recovery_fingerprint = hashlib.sha256(raw).hexdigest()
        if payload.get("kind") not in {"deterministic-policy-case", "captured-live"}:
            raise ValueError("Replay kind 必须明确标记证据来源")
        responses = payload.get("responses")
        if not isinstance(responses, list) or not responses:
            raise ValueError("Replay 必须包含非空 responses")
        self.model_name = str(payload.get("model", "replay"))
        self._responses = responses

    def new_session(self, tool_schemas: Sequence[dict[str, Any]]) -> ModelSession:
        # 每个任务从同一份只读 Fixture 的第一步开始，任务之间不共享会话状态。
        return ReplayModelSession(list(self._responses))

    def new_session_at(self, tool_schemas: Sequence[dict[str, Any]], cursor: int) -> ModelSession:
        # 包括结果不明的预留调用；接续不能倒回第一条重放工具意图。
        return ReplayModelSession(list(self._responses), cursor)
