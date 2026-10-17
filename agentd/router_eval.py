"""无 API Key 的 Jev 路由接线与成本算术夹具；不冒充模型质量实测。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .app.router import ChoiceJudgement, FakeChoiceSource, ModelPrice, ModelRouter

_FIXTURE = Path(__file__).resolve().parent / "testdata/router-v1.jsonl"
_PRICES = {
    "economy": ModelPrice(0.2, 0.8),
    "strong": ModelPrice(2.0, 8.0),
}
_ASSUMED_JEV_INPUT_TOKENS = 250
_ASSUMED_JEV_INPUT_USD_PER_MILLION = 0.042
_ASSUMED_JEV_LATENCY_MS = 60


class _NoCallGateway:
    def __init__(self, name: str) -> None:
        self.model_name = name

    def new_session(self, *_: Any) -> None:
        raise AssertionError("路由测评不调用模型")


def _cost(tier: str, usage: list[int]) -> float:
    price = _PRICES[tier]
    return (usage[0] * price.input_usd_per_million + usage[1] * price.output_usd_per_million) / 1_000_000


async def evaluate(path: Path = _FIXTURE) -> dict[str, Any]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("路由夹具为空或 id 重复")
    answers = {
        case["summary"]: (
            RuntimeError("fixture router failure") if case.get("fakeError")
            else ChoiceJudgement(case["fakeChoice"], case["confidence"], _ASSUMED_JEV_INPUT_TOKENS)
        )
        for case in cases
    }
    router = ModelRouter(
        {tier: _NoCallGateway(tier) for tier in _PRICES},
        FakeChoiceSource(answers), prices=_PRICES,
        router_input_usd_per_million=_ASSUMED_JEV_INPUT_USD_PER_MILLION,
    )
    totals = {
        mode: {"success": 0, "costUsd": 0.0, "assumedDownstreamLatencyMs": 0}
        for mode in ("fixedEconomy", "fixedStrong", "routed")
    }
    correct_tier = fallback_count = 0
    routes = []
    for case in cases:
        _, decision = await router.choose(case["summary"])
        correct_tier += decision.effective_tier == case["goldTier"]
        fallback_count += decision.fallback_reason is not None
        routes.append({"id": case["id"], **decision.as_trace()})
        for mode, tier in (
            ("fixedEconomy", "economy"),
            ("fixedStrong", "strong"),
            ("routed", decision.effective_tier),
        ):
            totals[mode]["success"] += bool(case[tier + "Success"])
            totals[mode]["costUsd"] += _cost(tier, case[tier + "Usage"])
            totals[mode]["assumedDownstreamLatencyMs"] += case[tier + "LatencyMs"]
    for item in totals.values():
        item["successRate"] = round(item["success"] / len(cases), 4)
        item["costUsd"] = round(item["costUsd"], 8)
        item["assumedDownstreamLatencyMs"] = round(item["assumedDownstreamLatencyMs"] / len(cases), 2)
    # 夹具假设即使分类报错也计入一次 Jev 请求；真实失败计费取决于服务端账单。
    assumed_jev_cost = round(
        len(cases) * _ASSUMED_JEV_INPUT_TOKENS * _ASSUMED_JEV_INPUT_USD_PER_MILLION / 1_000_000,
        8,
    )
    totals["routed"]["assumedJevCostUsd"] = assumed_jev_cost
    totals["routed"]["costUsd"] = round(totals["routed"]["costUsd"] + assumed_jev_cost, 8)
    totals["routed"]["assumedTotalLatencyMs"] = round(
        totals["routed"]["assumedDownstreamLatencyMs"] + _ASSUMED_JEV_LATENCY_MS, 2,
    )
    return {
        "kind": "deterministic-fixture-not-live-quality",
        "caseCount": len(cases),
        "effectiveTierAccuracy": round(correct_tier / len(cases), 4),
        "fallbackCount": fallback_count,
        "assumedPricesUsdPerMillionTokens": {
            tier: {"input": value.input_usd_per_million, "output": value.output_usd_per_million}
            for tier, value in _PRICES.items()
        },
        "assumedJevPerAttempt": {
            "inputTokens": _ASSUMED_JEV_INPUT_TOKENS,
            "inputUsdPerMillion": _ASSUMED_JEV_INPUT_USD_PER_MILLION,
            "latencyMs": _ASSUMED_JEV_LATENCY_MS,
            "chargedOnFixtureError": True,
        },
        "results": totals,
        "routes": routes,
    }


def main() -> None:
    print(json.dumps(asyncio.run(evaluate()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
