from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from agentd.app.model_gateway import ModelInvocation
from agentd.app.models import AlertEvent, ModelUsage
from agentd.app.router import (
    ChoiceJudgement, FakeChoiceSource, JevChoiceSource, ModelPrice, ModelRouter,
)
from agentd.app.runner import AgentRunner
from agentd.router_eval import evaluate


class _Gateway:
    mode = "replay"
    provider_name = "deterministic-fixture"
    capabilities = {"toolCalling": True, "deterministic": True}

    def __init__(self, name: str) -> None:
        self.model_name = name
        self.calls = 0

    def new_session(self, _schemas):
        self.calls += 1
        return self

    async def invoke(self, _messages):
        return ModelInvocation(
            AIMessage(content='{"summary":"done","rootCause":"fixture","severity":"info","recommendation":"none","injectionDetected":false}'),
            ModelUsage(inputTokens=100, outputTokens=50, totalTokens=150),
            "stop", 0,
        )


class _Sandbox:
    def __init__(self) -> None:
        self.released = []

    async def claim(self):
        return {"id": "fixture-sandbox"}

    async def release(self, sandbox_id):
        self.released.append(sandbox_id)


class _SdkClient:
    def __init__(self) -> None:
        self.calls = []

    async def system_one(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices={
            "model_tier": SimpleNamespace(choice="economy", confidence=0.92),
        })


class RouterTest(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_choice_shape_without_network(self) -> None:
        client = _SdkClient()
        judgement = await JevChoiceSource("unused", client).classify("list pods")
        self.assertEqual(judgement, ChoiceJudgement("economy", 0.92))
        self.assertEqual(client.calls[0]["state"], {"request": "list pods"})
        choice = client.calls[0]["questions"]["model_tier"]
        self.assertEqual(set(choice.criteria), {"economy", "strong"})

    async def test_fallbacks_and_cost(self) -> None:
        cheap, strong = _Gateway("cheap"), _Gateway("strong")
        router = ModelRouter(
            {"economy": cheap, "strong": strong},
            FakeChoiceSource({
                "good": ChoiceJudgement("economy", 0.9),
                "low": ChoiceJudgement("economy", 0.5),
                "bad": ChoiceJudgement("arbitrary-model", 1.0),
                "error": RuntimeError("secret details"),
            }),
            prices={"economy": ModelPrice(0.2, 0.8)},
        )
        self.assertIs((await router.choose("good"))[0], cheap)
        self.assertEqual((await router.choose("low"))[1].fallback_reason, "low_confidence")
        self.assertEqual((await router.choose("bad"))[1].fallback_reason, "invalid_choice")
        self.assertEqual((await router.choose("error"))[1].fallback_reason, "router_error")
        self.assertEqual((await router.choose("  "))[1].fallback_reason, "empty_request")
        self.assertEqual(router.estimated_model_cost("economy", ModelUsage(inputTokens=100, outputTokens=50)), 0.00006)
        self.assertIsNone(router.estimated_model_cost("economy", ModelUsage()))
        self.assertIsNone(router.estimated_model_cost("strong", ModelUsage()))

        class _Slow:
            source = "fake-slow"

            async def classify(self, _summary):
                await asyncio.sleep(0.1)
                return ChoiceJudgement("economy", 1.0)

        timeout_router = ModelRouter(
            {"economy": cheap, "strong": strong}, _Slow(), timeout_seconds=0.001,
        )
        self.assertEqual((await timeout_router.choose("slow"))[1].fallback_reason, "timeout")

    async def test_runner_uses_chosen_gateway_once_and_records_cost(self) -> None:
        with TemporaryDirectory(dir="/tmp") as directory:
            cheap, strong = _Gateway("cheap"), _Gateway("strong")
            sandbox = _Sandbox()
            router = ModelRouter(
                {"economy": cheap, "strong": strong},
                FakeChoiceSource({"list pods": ChoiceJudgement("economy", 0.9)}),
                prices={"economy": ModelPrice(0.2, 0.8)},
            )
            runner = AgentRunner(
                None, sandbox, strong,
                workspace_root=Path(directory), model_router=router,
            )
            diagnosis, trace, status = await runner.run(
                "task-router", AlertEvent(annotations={"summary": "list pods"}),
            )
            self.assertEqual(status, "succeeded")
            self.assertEqual(trace.model, "cheap")
            self.assertEqual(trace.routing["effectiveTier"], "economy")
            self.assertEqual(trace.routing["estimatedModelCostUsd"], 0.00006)
            self.assertIsNone(trace.routing["routerCostUsd"])
            self.assertEqual(cheap.calls, 1)
            self.assertEqual(strong.calls, 0)
            self.assertEqual(sandbox.released, ["fixture-sandbox"])

    async def test_deterministic_fixture_is_labeled(self) -> None:
        report = await evaluate()
        self.assertEqual(report["kind"], "deterministic-fixture-not-live-quality")
        self.assertEqual(report["caseCount"], 8)
        self.assertEqual(report["fallbackCount"], 2)
        self.assertEqual(report["results"]["routed"]["successRate"], 1.0)


if __name__ == "__main__":
    unittest.main()
