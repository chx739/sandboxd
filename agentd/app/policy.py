from __future__ import annotations

import json
import re
from typing import Any

MAX_ITERATIONS = 6
MAX_TOOL_CALLS = 8
MAX_PROMETHEUS_CALLS = 4
MAX_OBSERVATION_BYTES = 4 << 10
MAX_TASK_SECONDS = 120
DIAGNOSTIC_NAMESPACE = "sandboxd-target"

KUBERNETES_READ_OPERATIONS = {
    "list_pods",
    "get_deployment",
    "get_pod_logs",
    "get_configmap",
    "list_events",
}
KUBERNETES_KEYS = {
    "operation",
    "namespace",
    "name",
    "container",
    "tailLines",
    "previous",
}
PLAN_KEYS = {"namespace", "name", "replicas"}
DNS_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_prometheus",
            "description": "对固定 Prometheus 执行只读即时 PromQL 查询。不能指定 URL。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "只读 PromQL"}
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kubernetes_read",
            "description": (
                "通过 gVisor 沙箱读取 Kubernetes。允许 list_pods、get_deployment、"
                "get_pod_logs、get_configmap、list_events；禁止任何写操作。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "namespace": {"type": "string"},
                    "name": {"type": "string"},
                    "container": {"type": "string"},
                    "tailLines": {"type": "integer"},
                    "previous": {"type": "boolean"},
                },
                "required": ["operation", "namespace"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_plan",
            "description": "只提交 Deployment scale DryRun Plan；不会批准或执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "name": {"type": "string"},
                    "replicas": {"type": "integer", "minimum": 0, "maximum": 10},
                },
                "required": ["namespace", "name", "replicas"],
                "additionalProperties": False,
            },
        },
    },
]


def validate_tool_call(
    call: dict[str, Any],
    tool_calls_used: int,
    prometheus_calls_used: int,
) -> dict[str, Any]:
    name = str(call.get("name", ""))
    call_id = str(call.get("id", "")) or "missing-tool-call-id"
    arguments = call.get("args", {})
    result = {
        "id": call_id,
        "name": name,
        "args": arguments if isinstance(arguments, dict) else {},
        "allowed": False,
        "reason": "",
        "denyLayer": "agent-policy",
    }

    if tool_calls_used >= MAX_TOOL_CALLS:
        result["reason"] = "tool call 超过每任务 %d 次上限" % MAX_TOOL_CALLS
        return result
    if not isinstance(arguments, dict):
        result["reason"] = "tool arguments 必须是 JSON object"
        return result

    if name == "query_prometheus":
        if set(arguments) != {"query"}:
            result["reason"] = "query_prometheus 只接受 query"
            return result
        query = arguments.get("query")
        if not isinstance(query, str) or not query or len(query.encode()) > 2048:
            result["reason"] = "PromQL 必须是 1 到 2048 bytes"
            return result
        if prometheus_calls_used >= MAX_PROMETHEUS_CALLS:
            result["reason"] = "Prometheus 查询超过每任务 %d 次上限" % MAX_PROMETHEUS_CALLS
            return result
        result["allowed"] = True
        return result

    if name == "kubernetes_read":
        unknown = set(arguments) - KUBERNETES_KEYS
        if unknown:
            result["reason"] = "kubernetes_read 包含未知字段"
            return result
        operation = arguments.get("operation")
        if operation not in KUBERNETES_READ_OPERATIONS:
            result["reason"] = "Kubernetes operation 不在只读白名单: %s" % operation
            return result
        if arguments.get("namespace") != DIAGNOSTIC_NAMESPACE:
            result["reason"] = "namespace 不在诊断范围"
            return result
        for key in ("name", "container"):
            value = arguments.get(key)
            if value is not None and (
                not isinstance(value, str) or not value or not DNS_NAME.fullmatch(value)
            ):
                result["reason"] = "%s 不是合法 Kubernetes 名称" % key
                return result
        tail_lines = arguments.get("tailLines")
        if tail_lines is not None and (
            isinstance(tail_lines, bool)
            or not isinstance(tail_lines, int)
            or not 1 <= tail_lines <= 200
        ):
            result["reason"] = "tailLines 必须在 1 到 200 之间"
            return result
        result["allowed"] = True
        return result

    if name == "propose_plan":
        if set(arguments) != PLAN_KEYS:
            result["reason"] = "propose_plan 只接受 namespace、name、replicas"
            return result
        if arguments.get("namespace") != DIAGNOSTIC_NAMESPACE:
            result["reason"] = "Plan namespace 不在允许范围"
            return result
        target = arguments.get("name")
        replicas = arguments.get("replicas")
        if not isinstance(target, str) or not DNS_NAME.fullmatch(target):
            result["reason"] = "Plan Deployment name 无效"
            return result
        if isinstance(replicas, bool) or not isinstance(replicas, int) or not 0 <= replicas <= 10:
            result["reason"] = "Plan replicas 必须在 0 到 10 之间"
            return result
        result["allowed"] = True
        return result

    result["reason"] = "未知工具: %s" % name
    return result


def bounded_text(value: str, limit: int = MAX_OBSERVATION_BYTES) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore") + "...[truncated]"


def action_summary(name: str, arguments: dict[str, Any]) -> str:
    return bounded_text(
        name + " " + json.dumps(arguments, ensure_ascii=False, sort_keys=True),
        1024,
    )
