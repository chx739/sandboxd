from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AlertEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime | None = Field(default=None, alias="startsAt")
    fingerprint: str = ""


class AlertmanagerPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    status: str = "firing"
    group_key: str = Field(default="", alias="groupKey")
    alerts: list[AlertEvent] = Field(default_factory=list)


class ManualTaskRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    labels: dict[str, str] = Field(default_factory=dict)


class Evidence(BaseModel):
    source: str
    summary: str


class DeniedAction(BaseModel):
    action: str
    reason: str
    layer: str


class Diagnosis(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    summary: str
    root_cause: str = Field(default="", alias="rootCause")
    severity: str = "warning"
    evidence: list[Evidence] = Field(default_factory=list)
    injection_detected: bool = Field(default=False, alias="injectionDetected")
    denied_actions: list[DeniedAction] = Field(default_factory=list, alias="deniedActions")
    recommendation: str = ""
    plan_id: str | None = Field(default=None, alias="planId")


TaskStatus = Literal["queued", "running", "succeeded", "failed", "limit_exceeded"]

class ToolResult(BaseModel):
    """工具的模型通道与审计通道，避免把 Trace 结构直接回灌给 LLM。"""

    model_content: str
    audit_details: dict[str, Any] = Field(default_factory=dict)
    is_error: bool = False
    denied: bool = False
    deny_layer: str = ""


class ModelUsage(BaseModel):
    input_tokens: int = Field(default=0, alias="inputTokens")
    output_tokens: int = Field(default=0, alias="outputTokens")
    total_tokens: int = Field(default=0, alias="totalTokens")


class AgentEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    index: int
    type: str
    iteration: int | None = None
    tool: str = ""
    elapsed_ms: int = Field(default=0, alias="elapsedMs")
    details: dict[str, Any] = Field(default_factory=dict)


def sum_model_usage(items: list[ModelUsage]) -> ModelUsage:
    return ModelUsage(
        inputTokens=sum(item.input_tokens for item in items),
        outputTokens=sum(item.output_tokens for item in items),
        totalTokens=sum(item.total_tokens for item in items),
    )



class TraceStep(BaseModel):
    index: int
    node: str
    tool: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    plugin_id: str = Field(default="", alias="pluginId")
    plugin_version: str = Field(default="", alias="pluginVersion")
    denied: bool = False
    deny_layer: str = Field(default="", alias="denyLayer")
    observation: str = ""
    audit_details: dict[str, Any] = Field(default_factory=dict, alias="auditDetails")
    elapsed_ms: int = Field(default=0, alias="elapsedMs")


class AgentTrace(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    mode: str
    model: str
    provider: str = ""
    capabilities: dict[str, Any] = Field(default_factory=dict)
    plugins: list[dict[str, Any]] = Field(default_factory=list)
    model_usage: ModelUsage = Field(default_factory=ModelUsage, alias="modelUsage")
    sandbox_id: str | None = Field(default=None, alias="sandboxId")
    alert_fingerprint: str = Field(default="", alias="alertFingerprint")
    injected_via: list[str] = Field(default_factory=list, alias="injectedVia")
    steps: list[TraceStep] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    verdict: str = ""
    final: Diagnosis | None = None
    elapsed_ms: int = Field(default=0, alias="elapsedMs")


class AgentTask(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    status: TaskStatus
    alert: AlertEvent
    created_at: datetime = Field(default_factory=utc_now, alias="createdAt")
    updated_at: datetime = Field(default_factory=utc_now, alias="updatedAt")
    result: Diagnosis | None = None
    error: str | None = None
    trace: AgentTrace | None = None
