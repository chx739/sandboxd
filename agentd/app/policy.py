from __future__ import annotations

import json
import re
from typing import Any

from .clients import LINUX_READ_OPERATIONS
from .redaction import safe_tool_arguments
from .tools.files import MAX_FILE_BYTES

MAX_ITERATIONS = 6
MAX_TOOL_CALLS = 8
MAX_PROMETHEUS_CALLS = 4
MAX_OBSERVATION_BYTES = 4 << 10
# 任务预算是三段独立窗口：claim（获取沙箱）→ loop（模型与工具）→ release（清理）。
# MAX_TASK_SECONDS 只约束 Agent Loop；claim 不能共用这个预算——冷启动慢于 120s 时
# 取消一个服务端可能已创建成功的 claim，会得到没有 id 的 busy Pod，只能等
# Pod 的 1 小时 ActiveDeadlineSeconds 兜底回收。
MAX_TASK_SECONDS = 120
# claim 预算略高于 sandboxd 的 create-timeout（默认 3 分钟）加网络余量：
# 到达这个上限时服务端必然已经自己超时，此时取消不会丢掉真实存在的沙箱。
MAX_CLAIM_SECONDS = 190
# release 是清理窗口；父任务已取消时由独立 Task + shield 保证执行（见 runner.py）。
MAX_RELEASE_SECONDS = 10
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
TARGET_ID = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,62})$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
FILE_KEYS = {
    "list_files": {"path"},
    "read_file": {"path", "offset", "limit"},
    "search_files": {"query", "path"},
    "write_file": {"path", "content", "expectedSha256"},
    "edit_file": {"path", "oldText", "newText", "expectedSha256"},
}



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

    if name == "linux_read":
        if set(arguments) != {"targetId", "operation"}:
            result["reason"] = "linux_read 只接受 targetId 和 operation"
            return result
        target_id = arguments.get("targetId")
        if not isinstance(target_id, str) or not TARGET_ID.fullmatch(target_id):
            result["reason"] = "Linux targetId 格式无效"
            return result
        if arguments.get("operation") not in LINUX_READ_OPERATIONS:
            result["reason"] = "Linux operation 不在只读白名单"
            return result
        result["allowed"] = True
        return result

    if name in FILE_KEYS:
        if not set(arguments) <= FILE_KEYS[name]:
            result["reason"] = "%s 包含未知字段" % name
            return result
        path = arguments.get("path", ".")
        if not isinstance(path, str) or not path or len(path.encode("utf-8")) > 512:
            result["reason"] = "文件 path 无效或过长"
            return result
        if name != "list_files" and name != "search_files" and "path" not in arguments:
            result["reason"] = "%s 缺少 path" % name
            return result
        if name == "read_file":
            offset = arguments.get("offset", 0)
            limit = arguments.get("limit", 16384)
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or not 0 <= offset <= MAX_FILE_BYTES
                or isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= MAX_FILE_BYTES
            ):
                result["reason"] = "read_file offset/limit 超出范围"
                return result
        elif name == "search_files":
            query = arguments.get("query")
            if not isinstance(query, str) or not query or len(query.encode("utf-8")) > 256:
                result["reason"] = "search_files query 必须为 1 到 256 bytes"
                return result
        elif name == "write_file":
            content = arguments.get("content")
            expected = arguments.get("expectedSha256")
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
                result["reason"] = "write_file content 超过文本大小上限"
                return result
            if expected is not None and (
                not isinstance(expected, str) or not SHA256.fullmatch(expected)
            ):
                result["reason"] = "expectedSha256 格式无效"
                return result
        elif name == "edit_file":
            expected = arguments.get("expectedSha256")
            old_text = arguments.get("oldText")
            new_text = arguments.get("newText")
            if (
                not isinstance(expected, str)
                or not SHA256.fullmatch(expected)
                or not isinstance(old_text, str)
                or not old_text
                or not isinstance(new_text, str)
                or len(old_text.encode("utf-8")) > MAX_FILE_BYTES
                or len(new_text.encode("utf-8")) > MAX_FILE_BYTES
            ):
                result["reason"] = "edit_file 参数或 expectedSha256 无效"
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
        name
        + " "
        + json.dumps(
            safe_tool_arguments(name, arguments),
            ensure_ascii=False,
            sort_keys=True,
        ),
        1024,
    )
