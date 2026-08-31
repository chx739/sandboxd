from __future__ import annotations

from typing import Any, Sequence

from ..clients import HTTPResult
from .base import PluginContext, PluginManifest


class KubernetesPlugin:
    """通过 sandboxd 访问 Kubernetes 诊断和 Pending Plan。

    插件本身不持有 kubeconfig，也不能审批 Plan。kubernetes_read 最终在 gVisor
    沙箱内执行；propose_plan 只创建待审批对象，继续复用 Phase 2 的可信边界。
    """

    manifest = PluginManifest(
        plugin_id="kubernetes",
        version="1.0.0",
        description="通过 sandboxd/gVisor 读取 Kubernetes，并创建待审批扩缩容 Plan",
        capabilities=(
            "sandbox:claim",
            "kubernetes:read",
            "deployment-scale:propose",
        ),
    )

    _schemas: tuple[dict[str, Any], ...] = (
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
    )

    @property
    def tool_schemas(self) -> Sequence[dict[str, Any]]:
        return self._schemas

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> HTTPResult:
        if tool_name == "kubernetes_read":
            return await context.sandboxd.kubernetes_read(
                context.sandbox_id,
                arguments,
            )
        if tool_name == "propose_plan":
            return await context.sandboxd.propose_plan(arguments)
        raise ValueError("Kubernetes 插件不支持工具: %s" % tool_name)
