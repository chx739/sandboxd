from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from .models import EvalCase


DEFAULT_SUITE_PATH = Path(__file__).resolve().parent / "cases" / "v1.jsonl"
EXPECTED_V1_COUNTS = {"clean": 4, "attack": 12, "hard-negative": 4}


def load_cases(path: Path = DEFAULT_SUITE_PATH) -> list[EvalCase]:
    """逐行加载，错误必须带行号，避免一个坏案例让排查变成猜测。"""

    cases: list[EvalCase] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = EvalCase.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        if case.id in seen:
            raise ValueError(f"{path}:{line_number}: 重复 case id: {case.id}")
        _validate_case_semantics(case, path, line_number)
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise ValueError(f"{path}: 数据集为空")
    return cases


def validate_v1_shape(cases: list[EvalCase]) -> None:
    counts = Counter(case.kind for case in cases)
    if len(cases) != 20 or dict(counts) != EXPECTED_V1_COUNTS:
        raise ValueError(
            "v1 必须是 20 条且 clean/attack/hard-negative=4/12/4，实际=%s"
            % dict(counts)
        )
    required_sources = {
        "alert",
        "prometheus",
        "podlog",
        "configmap",
        "event",
        "linux_log",
        "file",
    }
    actual_sources = {case.source for case in cases}
    if not required_sources <= actual_sources:
        raise ValueError("v1 缺少来源: %s" % sorted(required_sources - actual_sources))


def _validate_case_semantics(case: EvalCase, path: Path, line_number: int) -> None:
    prefix = f"{path}:{line_number}: {case.id}: "
    if case.kind == "attack":
        if case.replay.attack_tool is None or not case.expected.forbidden_tools:
            raise ValueError(prefix + "attack 必须同时定义 attackTool 和 forbiddenTools")
    elif case.replay.attack_tool is not None or case.expected.forbidden_tools:
        raise ValueError(prefix + "非 attack 案例不能预置危险 Tool")

    if case.source == "alert" and case.replay.source_tool is not None:
        raise ValueError(prefix + "alert 内容直接进入初始上下文，不应配置 sourceTool")
    if case.source != "alert" and case.replay.source_tool is None:
        raise ValueError(prefix + "非 alert 来源必须通过真实 Tool Result 进入上下文")

    if case.kind in {"attack", "hard-negative"}:
        if case.source not in case.expected.injection_sources:
            raise ValueError(prefix + "必须声明非可信内容实际进入的 source")

    source_ref = tool_ref(case.replay.source_tool) if case.replay.source_tool else ""
    if source_ref and source_ref not in case.expected.required_tools:
        raise ValueError(prefix + "sourceTool 必须出现在 requiredTools")

    attack_ref = tool_ref(case.replay.attack_tool) if case.replay.attack_tool else ""
    if attack_ref and attack_ref not in case.expected.forbidden_tools:
        raise ValueError(prefix + "attackTool 必须出现在 forbiddenTools")


def tool_ref(spec: object) -> str:
    if spec is None:
        return ""
    name = str(getattr(spec, "name"))
    arguments = dict(getattr(spec, "arguments"))
    operation = arguments.get("operation")
    if isinstance(operation, str) and operation:
        return f"{name}:{operation}"
    if name in {"read_file", "write_file", "edit_file"}:
        path = arguments.get("path")
        if isinstance(path, str) and path:
            return f"{name}:{path}"
    return name
