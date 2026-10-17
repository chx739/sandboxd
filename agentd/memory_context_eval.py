"""无模型费用的长会话窗口对照；旧算法固定为本 PR 修改前的字符裁剪。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from .app.context import MAX_MODEL_CONTEXT_CHARS, transform_model_context
from .app.working_memory import evidence_id, project_working_memory, validate_update


def _chars(message: BaseMessage) -> int:
    size = len(str(message.content))
    if isinstance(message, AIMessage) and message.tool_calls:
        size += len(json.dumps(message.tool_calls, ensure_ascii=False, default=str))
    return size


def _previous_character_trim(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """复现修改前 context.py 的 48 KiB 组裁剪，以便稳定对照。"""
    source = list(messages)
    if sum(map(_chars, source)) <= MAX_MODEL_CONTEXT_CHARS:
        return source
    fixed = source[:2]
    groups: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []
    for message in source[2:]:
        if isinstance(message, AIMessage) and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)
    selected: list[list[BaseMessage]] = []
    used = sum(map(_chars, fixed))
    for group in reversed(groups):
        size = sum(map(_chars, group))
        if used + size <= MAX_MODEL_CONTEXT_CHARS or not selected:
            selected.append(group)
            used += size
        else:
            break
    return fixed + [message for group in reversed(selected) for message in group]


def _calls(messages: Sequence[BaseMessage]) -> list[tuple[str, str]]:
    return [(str(call.get("name")), json.dumps(call.get("args", {}), sort_keys=True))
            for message in messages if isinstance(message, AIMessage)
            for call in message.tool_calls if call.get("name") != "update_working_memory"]


def run() -> dict[str, object]:
    first_ref = evidence_id("task-long-context", "check-0")
    alert = {"labels": {"cluster": "demo", "namespace": "payments"},
             "annotations": {"summary": "排查 payments 内存告警"}}
    messages: list[BaseMessage] = [
        SystemMessage(content="安全规则：只按证据诊断。"),
        HumanMessage(content=json.dumps(alert, ensure_ascii=False)),
    ]
    denied_ref = ""
    for index in range(56):
        call_id = f"check-{index}"
        ref = evidence_id("task-long-context", call_id)
        denied = index == 10
        if denied:
            denied_ref = ref
        messages.extend([
            AIMessage(content="", tool_calls=[{
                "id": call_id, "name": "kubernetes_read",
                "args": {"operation": "get_deployment", "namespace": "payments",
                         "name": f"service-{index}"}, "type": "tool_call",
            }]),
            ToolMessage(content=json.dumps({
                "ok": not denied, "denied": denied,
                "denyLayer": "agent-policy" if denied else "",
                "evidenceId": ref, "observedAt": "2026-09-29T08:00:00+00:00",
                "body": {"metadata": {"uid": f"uid-{index}"},
                         "detail": "x" * 1300},
            }), tool_call_id=call_id),
        ])
        if index == 0:
            update = {"hypotheses": [{
                "text": "核对最早的 OOM 线索", "status": "open", "evidenceIds": [first_ref],
            }], "pendingChecks": ["核对该 Pod 的上一容器退出原因"]}
            messages.extend([
                AIMessage(content="", tool_calls=[{
                    "id": "remember-0", "name": "update_working_memory",
                    "args": update, "type": "tool_call",
                }]),
                ToolMessage(content=json.dumps({
                    "ok": True, "acceptedUpdate": validate_update(update, {first_ref}),
                }, ensure_ascii=False), tool_call_id="remember-0"),
            ])
    note = project_working_memory(alert, messages)
    old = _previous_character_trim(messages)
    new = transform_model_context(messages, working_memory=note,
                                  tool_schemas=[{"type": "function", "function": {
                                      "name": "kubernetes_read", "description": "read" * 400,
                                  }}])
    counts = Counter(_calls(messages))
    report: dict[str, object] = {
        "kind": "deterministic-synthetic-context-comparison",
        "priorAlgorithm": "PR HEAD context.py 48KiB character group trim",
        "externalModelCalls": 0,
        "sourceMessageCount": len(messages),
        "sourceChars": sum(map(_chars, messages)),
        "observedRepeatedChecks": sum(count - 1 for count in counts.values() if count > 1),
        "old": {
            "messageCount": len(old), "chars": sum(map(_chars, old)),
            "firstEvidenceVisible": any(first_ref in str(item.content) for item in old),
            "latestDenialVisible": any(denied_ref in str(item.content) for item in old),
        },
        "new": {
            "messageCount": len(new.messages), "chars": new.after_chars,
            "estimatedTokensWithOutputReserve": new.estimated_tokens,
            "toolSchemaEstimatedTokens": new.tool_schema_estimated_tokens,
            "firstEvidencePointerVisible": any(first_ref in str(item.content)
                                               for item in new.messages),
            "latestDenialVisible": any(denied_ref in str(item.content)
                                       for item in new.messages),
            "retainedHypothesis": bool(note["hypotheses"]),
            "retainedPendingCheck": bool(note["pendingChecks"]),
        },
        "interpretationLimit": (
            "重复检查数来自固定合成 transcript；没有真实模型调用，不能据此推断"
            "优化会降低 Live Agent 重复调用。"
        ),
    }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
