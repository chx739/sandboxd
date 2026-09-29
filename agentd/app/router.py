"""任务开始前选择固定模型；Jev 的判断永远不改变工具权限。"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .model_gateway import ModelGateway
from .models import ModelUsage

TIERS = ("economy", "strong")


def create_jev_client(api_key: str, *, ipv4_only: bool = False) -> Any:
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

    transport = None
    if ipv4_only:
        import httpx2

        # WSL 的 IPv6 路径不可用时，只为这个客户端选择 IPv4。
        transport = httpx2.AsyncHTTPTransport(local_address="0.0.0.0")
    return AsyncTypeSafeClient(
        api_key=api_key, transport=transport, timeout=2.0,
        retry=RetryPolicy(max_retries=0),
    )


@dataclass(frozen=True)
class ChoiceJudgement:
    tier: str
    confidence: float
    input_tokens: int | None = None


class ChoiceSource(Protocol):
    source: str

    async def classify(self, summary: str) -> ChoiceJudgement: ...


class JevChoiceSource:
    """只在显式配置 Live 时创建 SDK Client；测试可注入假 Client。"""

    source = "jev"

    def __init__(self, api_key: str, client: Any | None = None, *, ipv4_only: bool = False) -> None:
        self._api_key = api_key
        self._client = client
        self._ipv4_only = ipv4_only

    async def classify(self, summary: str) -> ChoiceJudgement:
        from typesafe_sdk import Choice

        question = Choice(
            instructions="Which model tier should handle this operations diagnosis request?",
            criteria={
                "economy": "Simple lookup or a direct single-step question with no ambiguous diagnosis.",
                "strong": "Multi-step diagnosis, ambiguous evidence, or a security-sensitive request.",
            },
        )
        if self._client is not None:
            response = await self._client.system_one(
                state={"request": summary}, questions={"model_tier": question},
            )
        else:
            async with create_jev_client(self._api_key, ipv4_only=self._ipv4_only) as client:
                response = await client.system_one(
                    state={"request": summary}, questions={"model_tier": question},
                )
        answer = response.choices["model_tier"]
        raw_tokens = response.usage.input_tokens
        tokens = raw_tokens if isinstance(raw_tokens, int) and raw_tokens >= 0 else None
        return ChoiceJudgement(str(answer.choice), float(answer.confidence), tokens)


class FakeChoiceSource:
    source = "deterministic-fake"

    def __init__(self, answers: Mapping[str, ChoiceJudgement | Exception]) -> None:
        self._answers = dict(answers)

    async def classify(self, summary: str) -> ChoiceJudgement:
        value = self._answers[summary]
        if isinstance(value, Exception):
            raise value
        return value


@dataclass(frozen=True)
class RouteDecision:
    requested_tier: str | None
    effective_tier: str
    confidence: float | None
    fallback_reason: str | None
    source: str
    elapsed_ms: int
    router_input_tokens: int | None = None

    def as_trace(self) -> dict[str, Any]:
        # 不记录原始请求、Key 或 SDK response body。
        return {
            "requestedTier": self.requested_tier,
            "effectiveTier": self.effective_tier,
            "confidence": self.confidence,
            "fallbackReason": self.fallback_reason,
            "source": self.source,
            "elapsedMs": self.elapsed_ms,
            "routerInputTokens": self.router_input_tokens,
        }


@dataclass(frozen=True)
class ModelPrice:
    input_usd_per_million: float
    output_usd_per_million: float


class ModelRouter:
    def __init__(
        self,
        gateways: Mapping[str, ModelGateway],
        source: ChoiceSource,
        *,
        default_tier: str = "strong",
        threshold: float = 0.7,
        timeout_seconds: float = 2.0,
        prices: Mapping[str, ModelPrice] | None = None,
        router_input_usd_per_million: float | None = None,
    ) -> None:
        if set(gateways) != set(TIERS) or default_tier not in TIERS:
            raise ValueError("路由只允许 economy/strong 两个静态 Gateway")
        if not 0 <= threshold <= 1 or timeout_seconds <= 0:
            raise ValueError("路由阈值或超时不合法")
        self._gateways = dict(gateways)
        self._source = source
        self._default = default_tier
        self._threshold = threshold
        self._timeout = timeout_seconds
        self._prices = dict(prices or {})
        self._router_input_price = router_input_usd_per_million
        if router_input_usd_per_million is not None and (
            not math.isfinite(router_input_usd_per_million)
            or router_input_usd_per_million < 0
        ):
            raise ValueError("Jev 输入单价不合法")
        if set(self._prices) - set(TIERS) or any(
            not math.isfinite(value)
            or value < 0
            for price in self._prices.values()
            for value in (price.input_usd_per_million, price.output_usd_per_million)
        ):
            raise ValueError("模型价格表不合法")

    def estimated_model_cost(self, tier: str, usage: ModelUsage) -> float | None:
        price = self._prices.get(tier)
        if price is None or (usage.input_tokens == 0 and usage.output_tokens == 0):
            return None
        return round((
            usage.input_tokens * price.input_usd_per_million
            + usage.output_tokens * price.output_usd_per_million
        ) / 1_000_000, 8)

    def estimated_router_cost(self, decision: RouteDecision) -> float | None:
        if self._router_input_price is None or decision.router_input_tokens is None:
            return None
        return round(decision.router_input_tokens * self._router_input_price / 1_000_000, 8)

    async def choose(self, summary: str) -> tuple[ModelGateway, RouteDecision]:
        started = time.monotonic()
        if not summary.strip():
            return self._gateways[self._default], RouteDecision(
                None, self._default, None, "empty_request",
                self._source.source, 0,
            )
        requested: str | None = None
        confidence: float | None = None
        router_input_tokens: int | None = None
        fallback: str | None = None
        try:
            # 类型标签是应用的静态表，Jev 只返回标签和置信度。
            judgement = await asyncio.wait_for(
                self._source.classify(summary[:512]), timeout=self._timeout,
            )
            requested = judgement.tier if judgement.tier in TIERS else None
            confidence = judgement.confidence
            if isinstance(judgement.input_tokens, int) and judgement.input_tokens >= 0:
                router_input_tokens = judgement.input_tokens
            if requested is None or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                confidence = None
                fallback = "invalid_choice"
            elif confidence < self._threshold:
                fallback = "low_confidence"
        except asyncio.TimeoutError:
            fallback = "timeout"
        except Exception:
            fallback = "router_error"
        effective = self._default if fallback else requested
        assert effective is not None
        decision = RouteDecision(
            requested, effective, confidence, fallback,
            self._source.source, int((time.monotonic() - started) * 1000),
            router_input_tokens,
        )
        return self._gateways[effective], decision
