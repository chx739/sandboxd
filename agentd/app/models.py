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


class TraceStep(BaseModel):
    index: int
    node: str
    tool: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    denied: bool = False
    deny_layer: str = Field(default="", alias="denyLayer")
    observation: str = ""
    elapsed_ms: int = Field(default=0, alias="elapsedMs")


class AgentTrace(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    mode: str
    model: str
    sandbox_id: str | None = Field(default=None, alias="sandboxId")
    alert_fingerprint: str = Field(default="", alias="alertFingerprint")
    injected_via: list[str] = Field(default_factory=list, alias="injectedVia")
    steps: list[TraceStep] = Field(default_factory=list)
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
