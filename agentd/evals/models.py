from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CaseKind = Literal["clean", "attack", "hard-negative"]
UntrustedSource = Literal[
    "alert",
    "prometheus",
    "podlog",
    "configmap",
    "event",
    "linux_log",
    "file",
]


class ToolCallSpec(BaseModel):
    """数据集中的一次确定性 Tool Call，不包含任何真实凭据。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict, alias="args")


class ReplaySpec(BaseModel):
    """Replay 只固定模型决策；Tool、Policy 和 Workspace 仍走真实代码。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_tool: ToolCallSpec | None = Field(default=None, alias="sourceTool")
    attack_tool: ToolCallSpec | None = Field(default=None, alias="attackTool")


class ExpectedSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    required_tools: list[str] = Field(default_factory=list, alias="requiredTools")
    forbidden_tools: list[str] = Field(default_factory=list, alias="forbiddenTools")
    injection_sources: list[UntrustedSource] = Field(
        default_factory=list,
        alias="injectionSources",
    )
    expected_deny_layers: list[str] = Field(
        default_factory=list,
        alias="expectedDenyLayers",
    )
    canary: str = ""


class EvalCase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{5,63}$")
    kind: CaseKind
    source: UntrustedSource
    benign_task: str = Field(min_length=1, max_length=1000, alias="benignTask")
    artifact: str = Field(min_length=1, max_length=8192)
    replay: ReplaySpec
    expected: ExpectedSpec


class EvalOutcome(BaseModel):
    """一次实际运行的规范化结果；不保存完整 Prompt 或 Tool 正文。"""

    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    mode: str
    task_succeeded: bool = Field(alias="taskSucceeded")
    requested_tools: list[str] = Field(default_factory=list, alias="requestedTools")
    executed_tools: list[str] = Field(default_factory=list, alias="executedTools")
    blocked_tools: list[str] = Field(default_factory=list, alias="blockedTools")
    deny_layers: list[str] = Field(default_factory=list, alias="denyLayers")
    injection_sources: list[str] = Field(default_factory=list, alias="injectionSources")
    external_state_changes: list[str] = Field(
        default_factory=list,
        alias="externalStateChanges",
    )
    canary_leaked: bool = Field(default=False, alias="canaryLeaked")
    canary_echoed: bool = Field(default=False, alias="canaryEchoed")
    refused: bool = False
    sandbox_released: bool = Field(default=False, alias="sandboxReleased")
    model_calls: int = Field(default=0, alias="modelCalls")
    input_tokens: int = Field(default=0, alias="inputTokens")
    output_tokens: int = Field(default=0, alias="outputTokens")
    total_tokens: int = Field(default=0, alias="totalTokens")
    error: str = ""


class CaseScore(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    kind: CaseKind
    clean_task_success: bool = Field(alias="cleanTaskSuccess")
    agent_attack_success: bool = Field(alias="agentAttackSuccess")
    unauthorized_side_effect: bool = Field(alias="unauthorizedSideEffect")
    contained: bool
    over_refusal: bool = Field(alias="overRefusal")
    required_tools: list[str] = Field(alias="requiredTools")
    covered_tools: list[str] = Field(alias="coveredTools")
    missing_tools: list[str] = Field(alias="missingTools")
    contract_errors: list[str] = Field(default_factory=list, alias="contractErrors")
    outcome: EvalOutcome


class Metric(BaseModel):
    numerator: int
    denominator: int
    rate: float


class EvalReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    suite: str
    mode: str
    case_count: int = Field(alias="caseCount")
    metrics: dict[str, Metric]
    results: list[CaseScore]
