from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from agentd.app.model_gateway import LiveModelGateway
from agentd.app.redaction import public_error

from .models import EvalCase, EvalOutcome
from .replay_runner import run_eval_case


async def run_live_suite(
    cases: list[EvalCase],
    gateway: LiveModelGateway,
) -> list[EvalOutcome]:
    """逐条运行真实模型；单条失败要记录并继续，不能丢掉选择性失败样本。"""

    outcomes: list[EvalOutcome] = []
    with TemporaryDirectory(prefix="sandboxd-live-eval-", dir="/tmp") as temporary:
        root = Path(temporary)
        for index, case in enumerate(cases, 1):
            print(f"[live {index}/{len(cases)}] {case.id}", flush=True)
            try:
                outcome = await run_eval_case(case, root, gateway)
            except Exception as exc:
                # 错误只保留脱敏摘要。API Key 永远不进入 Outcome、报告或控制台。
                outcome = EvalOutcome(
                    caseId=case.id,
                    mode="live",
                    taskSucceeded=False,
                    error="%s: %s" % (type(exc).__name__, public_error(exc)),
                )
            outcomes.append(outcome)
    return outcomes
