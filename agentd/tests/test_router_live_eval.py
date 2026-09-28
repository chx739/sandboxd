from __future__ import annotations

import json
import unittest

from langchain_core.messages import AIMessage

from agentd.app.model_gateway import LiveModelSession, ModelInvocation
from agentd.app.models import ModelUsage
from agentd.app.router import ChoiceJudgement, FakeChoiceSource, ModelPrice
from agentd.router_live_eval import evaluate_live, load_cases, plan


class _Gateway:
    mode = "fake"
    provider_name = "deterministic-fixture"
    capabilities = {}

    def __init__(self, tier: str, gold: dict[str, str], gold_tiers: dict[str, str]) -> None:
        self.model_name = "fixture-" + tier
        self.tier = tier
        self.gold = gold
        self.gold_tiers = gold_tiers
        self.calls = 0

    def new_session(self, _schemas):
        return self

    async def invoke(self, messages):
        self.calls += 1
        task = json.loads(messages[1].content)["task"]
        code = self.gold[task] if self.tier == "strong" or self.gold_tiers[task] == "economy" else "wrong"
        return ModelInvocation(
            AIMessage(content=json.dumps({"code": code})),
            ModelUsage(inputTokens=100, outputTokens=20, totalTokens=120),
            "stop", 0,
        )


class RouterLiveEvalTest(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_without_tools_keeps_plain_model(self) -> None:
        class _PlainModel:
            def bind_tools(self, _schemas):
                raise AssertionError("空工具列表不应强制模型绑定 Tool Calling")

            async def ainvoke(self, _messages):
                return AIMessage(content='{"code":"running"}')

        result = await LiveModelSession(_PlainModel(), []).invoke([])
        self.assertEqual(result.message.content, '{"code":"running"}')

    async def test_dry_plan_and_same_cases_three_modes(self) -> None:
        cases = load_cases()
        prices = {tier: ModelPrice(1, 2) for tier in ("economy", "strong")}
        preview = plan(cases, prices, 0.042)
        self.assertEqual(preview["maximumJevRequests"], 8)
        self.assertEqual(preview["maximumDownstreamRequests"], 24)
        self.assertLess(preview["worstCaseReservationUsd"], 1)

        gold = {case["summary"]: case["goldCode"] for case in cases}
        tiers = {case["summary"]: case["goldTier"] for case in cases}
        gateways = {tier: _Gateway(tier, gold, tiers) for tier in ("economy", "strong")}
        source = FakeChoiceSource({
            case["summary"]: ChoiceJudgement(case["goldTier"], 0.95, 200)
            for case in cases
        })
        report = await evaluate_live(cases, gateways, source, prices, 0.042, 1.0)
        self.assertEqual(report["kind"], "live-synthetic-operations-model-choice-eval")
        self.assertEqual(report["results"]["fixedEconomy"]["correct"], 4)
        self.assertEqual(report["results"]["fixedStrong"]["correct"], 8)
        self.assertEqual(report["results"]["routed"]["correct"], 8)
        self.assertEqual(report["routingTierAccuracy"], 1.0)
        self.assertEqual(report["jevAttempts"], 8)
        self.assertEqual(report["downstreamAttempts"], 24)
        self.assertGreater(report["results"]["routed"]["routerAccountedCostUsd"], 0)
        self.assertEqual(gateways["economy"].calls + gateways["strong"].calls, 24)
        self.assertLess(report["accountedCostUsd"], 1)

    async def test_budget_rejects_before_any_external_call(self) -> None:
        cases = load_cases(limit=1)
        gold = {case["summary"]: case["goldCode"] for case in cases}
        tiers = {case["summary"]: case["goldTier"] for case in cases}
        gateways = {tier: _Gateway(tier, gold, tiers) for tier in ("economy", "strong")}
        source = FakeChoiceSource({cases[0]["summary"]: ChoiceJudgement("economy", 0.9)})
        with self.assertRaises(ValueError):
            await evaluate_live(cases, gateways, source, {
                tier: ModelPrice(1, 2) for tier in gateways
            }, 0.042, 0.00001)
        self.assertEqual(sum(gateway.calls for gateway in gateways.values()), 0)

    async def test_first_case_provider_failure_stops_remaining_cases(self) -> None:
        cases = load_cases()
        gold = {case["summary"]: case["goldCode"] for case in cases}
        tiers = {case["summary"]: case["goldTier"] for case in cases}
        gateways = {tier: _Gateway(tier, gold, tiers) for tier in ("economy", "strong")}
        source = FakeChoiceSource({
            case["summary"]: RuntimeError("fixture provider error") for case in cases
        })
        report = await evaluate_live(cases, gateways, source, {
            tier: ModelPrice(1, 2) for tier in gateways
        }, 0.042, 1.0)
        self.assertEqual(report["completedCases"], 1)
        self.assertEqual(report["jevAttempts"], 1)
        self.assertEqual(report["stoppedReason"], "first_case_provider_error")
        self.assertEqual(sum(gateway.calls for gateway in gateways.values()), 3)


if __name__ == "__main__":
    unittest.main()
