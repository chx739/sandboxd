"""有界调用审计；只保存可见结果和计数，不保存模型输入或隐藏思维。"""
from __future__ import annotations

import json
from typing import Any

from ..models import TraceStep
from ..redaction import redact_text, safe_tool_arguments


def _safe(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "[truncated]"
    if isinstance(value, str):
        return redact_text(value, 12288)[0]
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:128]:
            if not isinstance(key, str):
                raise ValueError("audit keys must be text")
            result[key] = "[REDACTED]" if key.lower() in {"authorization", "api_key", "password", "token", "secret"} else _safe(item, depth + 1)
        if len(value) > 128:
            result["_truncated"] = True
        return result
    if isinstance(value, list):
        return [_safe(item, depth + 1) for item in value[:128]] + (["[truncated]"] if len(value) > 128 else [])
    if value is None or type(value) in {bool, int, float}:
        return value
    raise ValueError("audit must contain JSON values")


def normalize_audit(audit: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(audit, dict):
        raise ValueError("invalid call audit")
    if audit.get("kind") == "model":
        if set(audit) != {"kind", "usage", "usageKnown", "elapsedMs", "finishReason"}:
            raise ValueError("invalid model audit fields")
        usage = audit["usage"]
        if not isinstance(usage, dict) or set(usage) != {"inputTokens", "outputTokens", "totalTokens"}:
            raise ValueError("invalid usage fields")
        if any(type(v) is not int or not 0 <= v <= 10**12 for v in usage.values()):
            raise ValueError("invalid usage counters")
        if type(audit["usageKnown"]) is not bool or type(audit["elapsedMs"]) is not int or not 0 <= audit["elapsedMs"] <= 3600000:
            raise ValueError("invalid usage provenance or duration")
        if not isinstance(audit["finishReason"], str) or len(audit["finishReason"]) > 128:
            raise ValueError("invalid finish reason")
        result = _safe(audit)
    elif audit.get("kind") == "tool":
        if set(audit) != {"kind", "step", "outcomeKnown"} or type(audit["outcomeKnown"]) is not bool:
            raise ValueError("invalid tool audit fields")
        step = TraceStep.model_validate(audit["step"])
        if not 1 <= step.index <= 12 or step.node != "execute_tool" or not 0 <= step.elapsed_ms <= 3600000:
            raise ValueError("invalid tool audit step")
        data = _safe(step.model_dump(mode="json", by_alias=True))
        data["arguments"] = _safe(safe_tool_arguments(step.tool, step.arguments))
        # observation通常是JSON文本；递归脱敏可以保留其证据ID与可解析性。
        try:
            data["observation"] = json.dumps(_safe(json.loads(step.observation)), ensure_ascii=False, separators=(",", ":"))
        except json.JSONDecodeError:
            data["observation"] = redact_text(step.observation, 12288)[0]
        result = {"kind": "tool", "step": data, "outcomeKnown": audit["outcomeKnown"]}
    else:
        raise ValueError("invalid audit kind")
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > 64 << 10:
        raise ValueError("call audit exceeds 64 KiB")
    return result
