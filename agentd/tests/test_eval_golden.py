from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from agentd.evals.loader import load_cases
from agentd.evals.replay_runner import run_replay_suite

# Golden Replay 基线：Harness 三次在 Live 跑完后才发现自身缺陷（资源身份缺失、
# 跨来源污染、嵌套解析误选）。聚合指标全绿不代表每个 case 的工具流没有漂移，
# 这里把 40 条 case 的确定性结果逐条钉死，任何静默变化都会让本地测试失败。
GOLDEN_PATH = (
    Path(__file__).resolve().parents[1] / "testdata" / "eval-replay-golden-v2.json"
)
GOLDEN_VERSION = 1
# 只保留确定性字段。token 统计跟 Provider 实现走，不进基线。
GOLDEN_FIELDS = (
    "caseId",
    "taskSucceeded",
    "requestedTools",
    "executedTools",
    "blockedTools",
    "denyLayers",
    "injectionSources",
    "externalStateChanges",
    "canaryEchoed",
    "refused",
    "sandboxReleased",
    "modelCalls",
    "error",
)


def _baseline(outcomes: list[object]) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for outcome in outcomes:
        payload = outcome.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
        cases.append({field: payload[field] for field in GOLDEN_FIELDS})
    return cases


class EvalReplayGoldenTest(unittest.IsolatedAsyncioTestCase):
    async def test_replay_baseline_matches_golden(self) -> None:
        cases = load_cases()
        outcomes = await run_replay_suite(cases)
        baseline = _baseline(outcomes)

        if os.environ.get("AGENTD_UPDATE_EVAL_GOLDEN"):
            GOLDEN_PATH.write_text(
                json.dumps(
                    {
                        "goldenVersion": GOLDEN_VERSION,
                        "suite": "prompt-injection-v2",
                        "mode": "eval-replay",
                        "cases": baseline,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            self.skipTest("golden 基线已重新生成，请人工检查 diff 后提交")

        self.assertTrue(
            GOLDEN_PATH.exists(),
            "缺少 golden 基线：AGENTD_UPDATE_EVAL_GOLDEN=1 运行本测试生成",
        )
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        self.assertEqual(golden["goldenVersion"], GOLDEN_VERSION)
        self.assertEqual(
            len(golden["cases"]),
            len(baseline),
            "case 数量与基线不一致：数据集被修改而基线未更新",
        )
        for expected, actual in zip(golden["cases"], baseline):
            self.assertEqual(
                actual,
                expected,
                "case %s 的 Replay 结果偏离 golden 基线" % actual.get("caseId"),
            )


if __name__ == "__main__":
    unittest.main()
