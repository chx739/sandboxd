"""从当前 Session 父链投影排障进度；原始消息始终是可回放来源。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from .redaction import public_error

_EVIDENCE_ID = re.compile(r"^ev-[a-f0-9]{20}$")
_CURRENT_TOOLS = {"kubernetes_read", "query_prometheus", "linux_read"}
_EVENT_TOOLS = {"search_logs", "aggregate_logs"}
_MAX_OBSERVATIONS = 16
_MAX_HYPOTHESES = 8
_MAX_CHECKS = 12
_MAX_NOTE_CHARS = 4096
_MAX_UPDATE_BYTES = 3000
CURRENT_STATE_TTL = timedelta(seconds=60)


class WorkingMemory(TypedDict):
    goal: str
    scope: dict[str, str]
    observations: list[dict[str, Any]]
    citedEvidence: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    completedChecks: list[dict[str, str]]
    pendingChecks: list[str]

WORKING_MEMORY_TOOL_SCHEMA: dict[str, Any] = {"type": "function", "function": {
    "name": "update_working_memory",
    "description": "记录当前事故分支的待验证假设和下一步；引用仅限已经返回的 evidenceId。",
    "parameters": {
        "type": "object",
        "properties": {
            "hypotheses": {
                "type": "array", "maxItems": _MAX_HYPOTHESES,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "status": {"type": "string", "enum": ["open", "supported", "disfavored"]},
                        "evidenceIds": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["text", "status", "evidenceIds"],
                    "additionalProperties": False,
                },
            },
            "pendingChecks": {"type": "array", "maxItems": _MAX_CHECKS, "items": {"type": "string"}},
        },
        "additionalProperties": False,
    },
}}


def evidence_id(task_id: str, call_id: str) -> str:
    digest = hashlib.sha256((task_id + ":" + call_id).encode()).hexdigest()[:20]
    return "ev-" + digest


def scope_from_alert(alert: dict[str, Any]) -> dict[str, str]:
    labels = alert.get("labels", {})
    if not isinstance(labels, dict):
        return {}
    aliases = {
        "cluster": ("cluster",),
        "namespace": ("namespace",),
        "workload": ("workload", "deployment"),
        "component": ("component", "app"),
        "revision": ("revision", "version"),
    }
    result: dict[str, str] = {}
    for field, candidates in aliases.items():
        for candidate in candidates:
            value = labels.get(candidate)
            if isinstance(value, str) and value.strip():
                result[field] = public_error(value.strip(), limit=100)
                break
    return result


def _short(value: object, limit: int) -> str:
    return public_error(" ".join(str(value).split()), limit=limit)


def validate_update(value: object, allowed_evidence: set[str]) -> dict[str, Any]:
    """模型只能提议工作状态；引用必须来自当前分支已经返回的工具结果。"""
    if not isinstance(value, dict) or set(value) - {"hypotheses", "pendingChecks"}:
        raise ValueError("工作记忆更新字段不合法")
    hypotheses = value.get("hypotheses", [])
    checks = value.get("pendingChecks", [])
    if not isinstance(hypotheses, list) or len(hypotheses) > _MAX_HYPOTHESES:
        raise ValueError("假设数量不合法")
    if not isinstance(checks, list) or len(checks) > _MAX_CHECKS:
        raise ValueError("待检查项数量不合法")
    accepted: list[dict[str, Any]] = []
    for item in hypotheses:
        if not isinstance(item, dict) or set(item) - {"text", "status", "evidenceIds"}:
            raise ValueError("假设格式不合法")
        statement = _short(item.get("text", ""), 300)
        status = item.get("status", "open")
        refs = item.get("evidenceIds", [])
        if not statement or status not in {"open", "supported", "disfavored"}:
            raise ValueError("假设内容或状态不合法")
        if not isinstance(refs, list) or len(refs) > 5:
            raise ValueError("假设证据数量不合法")
        if any(not isinstance(ref, str) or ref not in allowed_evidence for ref in refs):
            raise ValueError("假设引用了当前分支不存在的证据")
        accepted.append({
            "text": statement, "status": status,
            "evidenceIds": list(dict.fromkeys(refs)),
        })
    pending = []
    for item in checks:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("待检查项格式不合法")
        pending.append(_short(item, 200))
    update: dict[str, Any] = {}
    if "hypotheses" in value:
        update["hypotheses"] = accepted
    if "pendingChecks" in value:
        update["pendingChecks"] = list(dict.fromkeys(pending))
    if len(json.dumps(update, ensure_ascii=False).encode("utf-8")) > _MAX_UPDATE_BYTES:
        raise ValueError("工作记忆更新超过工具结果预算")
    return update


def _resource_uid(body: object, depth: int = 0) -> str | None:
    if depth > 4:
        return None
    if isinstance(body, list):
        for item in body[:32]:
            found = _resource_uid(item, depth + 1)
            if found:
                return found
        return None
    if not isinstance(body, dict):
        return None
    metadata = body.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("uid"), str):
        return _short(metadata["uid"], 100)
    stdout = body.get("stdout")
    if (
        isinstance(stdout, str) and len(stdout) <= 64 << 10
        and body.get("outputTruncated") is not True
        and body.get("exitCode", 0) == 0
    ):
        try:
            decoded = json.loads(stdout)
        except ValueError:
            decoded = None
        found = _resource_uid(decoded, depth + 1)
        if found:
            return found
    for value in body.values():
        if isinstance(value, (dict, list)):
            found = _resource_uid(value, depth + 1)
            if found:
                return found
    return None


def resource_uid_from_tool_body(body: object) -> str | None:
    """仅解析成功且完整的 sandboxd JSON stdout；不把文本当规则。"""
    return _resource_uid(body)


def prometheus_sample_info(body: object) -> dict[str, Any]:
    """即时查询以样本时间判时效；未知/部分结果一律不标 current。"""
    if not isinstance(body, dict):
        return {"sampleTimeKnown": False}
    data = body.get("data")
    if not isinstance(data, dict):
        return {"sampleTimeKnown": False}
    result = data.get("result")
    result_type = data.get("resultType")
    if result_type in {"scalar", "string"} and isinstance(result, list):
        series: list[Any] = [{"value": result}]
    elif isinstance(result, list) and 0 < len(result) <= 256:
        series = result
    else:
        return {"sampleTimeKnown": False}
    observed: list[datetime] = []
    for item in series:
        if not isinstance(item, dict):
            return {"sampleTimeKnown": False}
        sample = item.get("value")
        if sample is None and isinstance(item.get("values"), list) and item["values"]:
            sample = item["values"][-1]
        if not isinstance(sample, list) or len(sample) < 2:
            return {"sampleTimeKnown": False}
        try:
            timestamp = datetime.fromtimestamp(float(sample[0]), timezone.utc)
        except (ValueError, TypeError, OverflowError):
            return {"sampleTimeKnown": False}
        observed.append(timestamp)
    return {
        "sampleTimeKnown": True,
        "oldestSampleAt": min(observed).isoformat(),
        "sampleCount": len(observed),
    }


def state_freshness(tool: str, payload: dict[str, Any], now: datetime) -> str:
    try:
        queried = datetime.fromisoformat(str(payload.get("observedAt", "")))
        if queried.tzinfo is None or not timedelta(0) <= now - queried <= CURRENT_STATE_TTL:
            return "stale"
        if tool == "query_prometheus":
            samples = payload if "sampleTimeKnown" in payload else prometheus_sample_info(payload.get("body"))
            if samples.get("sampleTimeKnown") is not True:
                return "unknown"
            sampled = datetime.fromisoformat(str(samples.get("oldestSampleAt", "")))
            if sampled.tzinfo is None or not timedelta(0) <= now - sampled <= CURRENT_STATE_TTL:
                return "stale"
        return "current"
    except (ValueError, TypeError):
        return "stale"


def stale_state_evidence(messages: Sequence[BaseMessage]) -> list[str]:
    """最终输出门槛：同一只读查询的最新结果仍过期时，不能确认当前状态。"""
    calls: dict[str, dict[str, Any]] = {}
    latest: dict[str, tuple[str, dict[str, Any]]] = {}
    for message in messages:
        if isinstance(message, AIMessage):
            calls.update({str(call.get("id", "")): call for call in message.tool_calls})
        elif isinstance(message, ToolMessage):
            call = calls.get(str(message.tool_call_id), {})
            name = str(call.get("name", ""))
            args = call.get("args", {})
            if name not in _CURRENT_TOOLS or (
                name == "kubernetes_read" and isinstance(args, dict)
                and args.get("operation") in {"get_pod_logs", "list_events"}
            ):
                continue
            payload = _parse_json(message.content)
            if payload is None or payload.get("ok") is not True:
                continue
            key = name + json.dumps(args, sort_keys=True, default=str)
            # 不把同名但 UID 不同的资源刷新合并。
            uid = payload.get("resourceUid") or _resource_uid(payload.get("body"))
            if uid:
                key += ":" + str(uid)
            elif name == "kubernetes_read":
                key += ":unknown:" + str(payload.get("evidenceId", message.tool_call_id))
            latest[key] = (name, payload)
    now = datetime.now(timezone.utc)
    return [str(payload.get("evidenceId", "legacy-state"))
            for name, payload in latest.values()
            if state_freshness(name, payload, now) != "current"]


def _parse_json(content: object) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _final_json(content: object) -> dict[str, Any] | None:
    """与最终诊断一样接受围栏/前置文字，但不从内层对象误取更新。"""
    if not isinstance(content, str):
        return None
    decoder = json.JSONDecoder()
    text = content[:64 << 10]
    offset = 0
    last: dict[str, Any] | None = None
    while offset < len(text):
        start = text.find("{", offset)
        if start < 0:
            break
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            offset = start + 1
            continue
        if isinstance(value, dict):
            last = value
        offset = start + max(consumed, 1)
    return last


def _event_times(body: object) -> list[str]:
    if not isinstance(body, dict):
        return []
    rows = body.get("logs", body.get("events", []))
    if not isinstance(rows, list):
        return []
    return [str(row["timestamp"])[:40] for row in rows[:3]
            if isinstance(row, dict) and isinstance(row.get("timestamp"), str)]


def available_evidence_ids(messages: Sequence[BaseMessage]) -> set[str]:
    """校验引用时扫描整条父链；有界显示视图不能缩小合法证据集合。"""
    known: set[str] = set()
    calls: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            calls.update(str(call.get("id", "")) for call in message.tool_calls
                         if isinstance(call, dict) and call.get("name") != "update_working_memory")
        elif isinstance(message, ToolMessage) and str(message.tool_call_id) in calls:
            payload = _parse_json(message.content)
            ref = payload.get("evidenceId") if payload else None
            if isinstance(ref, str) and _EVIDENCE_ID.fullmatch(ref):
                known.add(ref)
    return known


def project_working_memory(
    alert: dict[str, Any], messages: Sequence[BaseMessage],
    *, now: datetime | None = None,
) -> WorkingMemory:
    """只读投影：传入选中父链，就不会读到兄弟分支。"""
    instant = now or datetime.now(timezone.utc)
    annotations = alert.get("annotations", {})
    goal = annotations.get("summary", "") if isinstance(annotations, dict) else ""
    observations: list[dict[str, Any]] = []
    completed: list[dict[str, str]] = []
    hypotheses: list[dict[str, Any]] = []
    pending: list[str] = []
    calls: dict[str, dict[str, Any]] = {}
    known: set[str] = set()

    def apply_update(raw: object) -> None:
        nonlocal hypotheses, pending
        try:
            update = validate_update(raw, known)
        except ValueError:
            return
        for item in update.get("hypotheses", []):
            hypotheses = [old for old in hypotheses if old["text"] != item["text"]]
            hypotheses.append({**item, "provenance": "model-proposed", "verification": "pending"})
        if isinstance(raw, dict) and "pendingChecks" in raw:
            pending = update["pendingChecks"]

    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if isinstance(call, dict):
                    calls[str(call.get("id", ""))] = call
            final = _final_json(message.content) if not message.tool_calls else None
            if final and "workingMemoryUpdate" in final:
                apply_update(final["workingMemoryUpdate"])
        elif isinstance(message, ToolMessage):
            payload = _parse_json(message.content)
            if payload is None:
                continue
            call = calls.get(str(message.tool_call_id), {})
            name = str(call.get("name", ""))
            if name == "update_working_memory":
                if payload.get("ok") is True:
                    apply_update(payload.get("acceptedUpdate"))
                continue
            ref = payload.get("evidenceId")
            if not isinstance(ref, str) or not _EVIDENCE_ID.fullmatch(ref):
                continue
            known.add(ref)
            observed_at = str(payload.get("observedAt", ""))
            arguments = call.get("args", {})
            category = (
                "current_state" if name in _CURRENT_TOOLS else
                "historical_event" if name in _EVENT_TOOLS else "reference"
            )
            if (
                name == "kubernetes_read" and isinstance(arguments, dict)
                and arguments.get("operation") in {"get_pod_logs", "list_events"}
            ):
                category = "historical_event"
            if payload.get("ok") is not True:
                category = "attempt"
            freshness = "historical" if category == "historical_event" else "reference"
            if category == "attempt":
                freshness = "unavailable"
            if category == "current_state":
                freshness = state_freshness(name, payload, instant)
            body = payload.get("body")
            task_scope = scope_from_alert(alert)
            observation_scope: dict[str, str] = {}
            if isinstance(arguments, dict):
                for field in ("cluster", "namespace", "workload"):
                    value = arguments.get(field)
                    if isinstance(value, str) and value:
                        observation_scope[field] = _short(value, 100)
                if name == "kubernetes_read" and arguments.get("operation") == "get_deployment":
                    target = arguments.get("name")
                    if isinstance(target, str) and target:
                        observation_scope["workload"] = _short(target, 100)
            if isinstance(body, dict):
                cluster = body.get("cluster")
                if isinstance(cluster, str) and cluster:
                    observation_scope["cluster"] = _short(cluster, 100)
                metadata = body.get("metadata")
                if isinstance(metadata, dict):
                    namespace = metadata.get("namespace")
                    if isinstance(namespace, str) and namespace:
                        observation_scope["namespace"] = _short(namespace, 100)
            resource_uid = payload.get("resourceUid") or _resource_uid(body)
            labels = alert.get("labels", {})
            target_uid = (
                labels.get("resource_uid", labels.get("uid"))
                if isinstance(labels, dict) else None
            )
            mismatch = any(task_scope.get(field) not in {None, value}
                           for field, value in observation_scope.items()) or (
                isinstance(target_uid, str) and resource_uid is not None
                and target_uid != resource_uid
            )
            identity = "mismatch" if mismatch else (
                "matched" if task_scope.get("cluster") and
                observation_scope.get("cluster") == task_scope["cluster"] and (
                    task_scope.get("namespace") and task_scope.get("workload") and
                    observation_scope.get("namespace") == task_scope["namespace"] and
                    observation_scope.get("workload") == task_scope["workload"] or
                    isinstance(target_uid, str) and target_uid == resource_uid
                )
                else "unverified"
            )
            observations.append({
                "evidenceId": ref,
                "tool": name,
                "observedAt": observed_at,
                "oldestSampleAt": (
                    payload.get("oldestSampleAt") or prometheus_sample_info(body).get("oldestSampleAt")
                ) if name == "query_prometheus" else None,
                "category": category,
                "freshness": freshness,
                "eventTimes": _event_times(body) if category == "historical_event" else [],
                "queryWindow": {
                    key: _short(arguments[key], 40) for key in ("start", "end")
                    if isinstance(arguments, dict) and isinstance(arguments.get(key), str)
                } if category == "historical_event" else {},
                "resourceUid": resource_uid,
                "summary": _short(body if body is not None else payload.get("error", ""), 260),
                "scope": observation_scope,
                "identity": identity,
            })
            completed.append({
                "tool": name, "evidenceId": ref,
                "outcome": "denied" if payload.get("denied") else
                           "ok" if payload.get("ok") else "failed",
            })
    referenced = {ref for item in hypotheses[-_MAX_HYPOTHESES:]
                  for ref in item["evidenceIds"]}
    cited = [{key: item[key] for key in (
        "evidenceId", "tool", "observedAt", "oldestSampleAt", "category", "freshness",
        "resourceUid", "identity",
    )} for item in observations if item["evidenceId"] in referenced][-8:]
    note: WorkingMemory = {
        "goal": _short(goal, 300),
        "scope": scope_from_alert(alert),
        "observations": observations[-_MAX_OBSERVATIONS:],
        "citedEvidence": cited,
        "hypotheses": hypotheses[-_MAX_HYPOTHESES:],
        "completedChecks": completed[-_MAX_CHECKS:],
        "pendingChecks": pending[-_MAX_CHECKS:],
    }
    # 只裁剪投影视图，不改写 Session 中的原始工具记录。
    while len(json.dumps(note, ensure_ascii=False)) > _MAX_NOTE_CHARS:
        if note["observations"]:
            note["observations"].pop(0)
        elif note["completedChecks"]:
            note["completedChecks"].pop(0)
        elif note["hypotheses"]:
            note["hypotheses"].pop(0)
        elif note["pendingChecks"]:
            note["pendingChecks"].pop(0)
        elif note["citedEvidence"]:
            note["citedEvidence"].pop(0)
        else:
            break
    return note
