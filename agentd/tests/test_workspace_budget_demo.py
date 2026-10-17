from __future__ import annotations

import unittest

from agentd.workspace_budget_demo import fixture
from agentd.workspace_boundary_demo import invalid_operations


class WorkspaceBudgetFixtureTests(unittest.TestCase):
    def test_budget_fixture_reserves_last_slot_without_seventh_response(self):
        replay = fixture("budget")
        self.assertEqual(replay["kind"], "deterministic-policy-case")
        self.assertEqual(len(replay["responses"]), 6)
        for response in replay["responses"][:-1]:
            self.assertEqual(response["toolCalls"][0]["name"], "list_files")
        self.assertEqual(replay["responses"][-1]["delayMs"], 60000)
        self.assertNotIn("toolCalls", replay["responses"][-1])

    def test_deadline_fixture_leaves_call_quota_available(self):
        replay = fixture("expired")
        self.assertEqual(len(replay["responses"]), 2)
        self.assertEqual(replay["responses"][-1]["delayMs"], 60000)

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaises(ValueError):
            fixture("reset-deadline")

    def test_boundary_fixture_has_bounded_unique_cases_and_oversize_input(self):
        cases = invalid_operations()
        self.assertEqual(len({name for name, _ in cases}), len(cases))
        oversize = dict(cases)["oversize-text"]["content"]
        self.assertEqual(len(oversize), (256 << 10) + 1)
        self.assertLess(sum(len(json_value.get("content") or "") for _, json_value in cases), 300000)
