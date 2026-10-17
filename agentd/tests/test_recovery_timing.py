from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentd.app.runtime.durable import JournalCorrupt
from agentd.app.runtime.recovery import RecoveryConflict, RecoveryLedger, RecoveryLimit


class RecoveryTimingTests(unittest.TestCase):
    """固定时钟的纯逻辑证据；不是进程强杀或真实 Pod 接管验收。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="sandboxd-phase-clock-", dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "control" / "tasks.jsonl"
        self.ledger = RecoveryLedger(self.path)
        self.addCleanup(lambda: self.ledger.close())
        self.now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.ledger.create(task_id="task-clock", session_id="session-clock", branch_id="branch-main",
                           workspace_id="workspace-clock", alert={})

    def begin(self, seconds: float = 0) -> str:
        return self.ledger.begin_attempt("task-clock", self.now + timedelta(seconds=seconds))["attempts"][-1]["attemptId"]

    def reopen(self) -> None:
        self.ledger.close()
        self.ledger = RecoveryLedger(self.path)

    def bind(self, attempt: str) -> None:
        self.ledger.bind("task-clock", attempt, "sandbox-clock", "pod-clock")

    def test_queued_time_does_not_consume_unstarted_claim_window(self) -> None:
        self.reopen()
        later = self.now + timedelta(days=1)
        state = self.ledger.candidates(later)[0]
        self.assertIsNone(state["deadline"])
        with self.assertRaises(RecoveryConflict):
            self.ledger.remaining_seconds("task-clock", later)
        self.ledger.begin_attempt("task-clock", later)
        self.assertEqual(self.ledger.remaining_seconds("task-clock", later), 190)

    def test_claim_recovery_uses_original_190_seconds(self) -> None:
        old = self.begin()
        self.ledger.owner_stopped("task-clock", old, "never-bound")
        self.reopen()
        self.begin(185)
        self.assertEqual(self.ledger.remaining_seconds("task-clock", self.now + timedelta(seconds=185)), 5)
        with self.assertRaises(RecoveryLimit):
            self.ledger.remaining_seconds("task-clock", self.now + timedelta(seconds=190))

    def test_loop_has_separate_budget_and_downtime_is_not_refunded(self) -> None:
        first = self.begin()
        self.bind(first)
        before = self.ledger.begin_loop("task-clock", first, self.now + timedelta(seconds=180))
        self.assertEqual(before["deadline"], (self.now + timedelta(seconds=300)).isoformat())
        self.ledger.owner_stopped("task-clock", first, "containers-terminated")
        self.reopen()
        second = self.begin(250)
        self.bind(second)
        after = self.ledger.begin_loop("task-clock", second, self.now + timedelta(seconds=260))
        self.assertEqual(after["deadline"], before["deadline"])
        self.assertEqual(after["timing"], before["timing"])
        self.assertEqual(self.ledger.remaining_seconds("task-clock", self.now + timedelta(seconds=260)), 40)

    def test_expired_claim_cannot_start_a_fresh_loop(self) -> None:
        attempt = self.begin()
        self.bind(attempt)
        with self.assertRaises(RecoveryLimit):
            self.ledger.begin_loop("task-clock", attempt, self.now + timedelta(seconds=190))
        self.assertIsNone(self.ledger.get("task-clock")["timing"]["loopStartedAt"])

    def test_loop_requires_a_bound_current_attempt_and_cancel_wins(self) -> None:
        attempt = self.begin()
        with self.assertRaises(RecoveryConflict):
            self.ledger.begin_loop("task-clock", attempt, self.now)
        self.bind(attempt)
        self.ledger.cancel("task-clock")
        with self.assertRaises(RecoveryConflict):
            self.ledger.begin_loop("task-clock", attempt, self.now)

    def test_expired_loop_allows_cleanup_but_never_new_calls(self) -> None:
        attempt = self.begin()
        self.bind(attempt)
        self.ledger.begin_loop("task-clock", attempt, self.now)
        later = self.now + timedelta(seconds=121)
        self.reopen()
        self.assertEqual(self.ledger.candidates(later), [])
        with self.assertRaises(RecoveryLimit):
            self.ledger.reserve("task-clock", attempt, "model", later)
        self.ledger.owner_stopped("task-clock", attempt, "containers-terminated")
        self.ledger.finish("task-clock", "limit_exceeded")
        self.assertEqual(self.ledger.get("task-clock")["status"], "limit_exceeded")

    def test_clock_configuration_is_bounded_and_immutable(self) -> None:
        state = self.ledger.get("task-clock")
        state["timing"]["claimSeconds"] = 600
        with self.assertRaises(JournalCorrupt):
            self.ledger._save(state)
        for invalid in (0, -1, 601, True, 2.5):
            with self.assertRaises(JournalCorrupt):
                self.ledger.create(task_id="task-invalid", session_id="session-test", branch_id="branch-test",
                                   workspace_id="workspace-test", alert={}, claim_seconds=invalid)

    def test_clock_anchor_or_derived_deadline_cannot_be_rewritten(self) -> None:
        attempt = self.begin()
        self.bind(attempt)
        self.ledger.begin_loop("task-clock", attempt, self.now + timedelta(seconds=10))
        state = self.ledger.get("task-clock")
        state["timing"]["loopStartedAt"] = (self.now + timedelta(seconds=20)).isoformat()
        state["deadline"] = (self.now + timedelta(seconds=140)).isoformat()
        with self.assertRaises(JournalCorrupt):
            self.ledger._save(state)
        state = self.ledger.get("task-clock")
        state["deadline"] = (self.now + timedelta(seconds=500)).isoformat()
        with self.assertRaises(JournalCorrupt):
            self.ledger._save(state)

    def test_legacy_deadline_is_read_without_rewriting_or_extending(self) -> None:
        old = self.ledger.create(task_id="task-old", session_id="session-old", branch_id="branch-old",
                                 workspace_id="workspace-old", alert={}, deadline=self.now + timedelta(seconds=50))
        self.reopen()
        self.assertEqual(self.ledger.get("task-old"), old)
        attempt = self.ledger.begin_attempt("task-old", self.now)["attempts"][-1]["attemptId"]
        self.ledger.bind("task-old", attempt, "sandbox-old", "pod-old")
        current = self.ledger.begin_loop("task-old", attempt, self.now + timedelta(seconds=40))
        self.assertEqual(current["schemaVersion"], 1)
        self.assertEqual(current["deadline"], old["deadline"])
        self.assertNotIn("timing", current)
        self.assertEqual(self.ledger.remaining_seconds("task-old", self.now + timedelta(seconds=40)), 10)

    def test_model_and_tool_cannot_run_before_loop_clock_is_committed(self) -> None:
        attempt = self.begin()
        for kind in ("model", "tool"):
            with self.assertRaises(RecoveryConflict):
                self.ledger.reserve("task-clock", attempt, kind, self.now)
        route = self.ledger.reserve("task-clock", attempt, "router", self.now)
        self.ledger.finish_call("task-clock", attempt, route)
        self.bind(attempt)
        self.ledger.begin_loop("task-clock", attempt, self.now)
        self.ledger.reserve("task-clock", attempt, "model", self.now)

    def test_real_kubernetes_uid_can_start_with_a_digit_and_binding_is_immutable(self) -> None:
        attempt = self.begin()
        uid = "7823c42d-a3cf-4fd1-aa4d-39d09dc4bea1"
        self.ledger.bind("task-clock", attempt, "sandbox-clock", uid)
        self.reopen()
        self.assertEqual(self.ledger.get("task-clock")["attempts"][0]["podUid"], uid)
        state = self.ledger.get("task-clock")
        state["attempts"][0]["podUid"] = "another-uid"
        with self.assertRaises(JournalCorrupt):
            self.ledger._save(state)
        self.ledger.owner_stopped("task-clock", attempt, "containers-terminated")
        self.reopen()
        state = self.ledger.get("task-clock")
        state["attempts"][0]["ownerStopped"] = False
        with self.assertRaises(JournalCorrupt):
            self.ledger._save(state)

    def test_startup_scan_includes_expired_and_cancelled_owners_for_cleanup(self) -> None:
        attempt = self.begin()
        self.bind(attempt)
        self.ledger.cancel("task-clock")
        self.reopen()
        later = self.now + timedelta(days=1)
        self.assertEqual(self.ledger.candidates(later), [])
        records = self.ledger.records()
        self.assertEqual(records[0]["status"], "cancelling")
        self.assertFalse(records[0]["attempts"][0]["ownerStopped"])
        records[0]["attempts"].clear()
        self.assertEqual(len(self.ledger.records()[0]["attempts"]), 1)

    def test_clock_rollback_stops_instead_of_refunding_time(self) -> None:
        self.begin()
        with self.assertRaises(RecoveryConflict):
            self.ledger.remaining_seconds("task-clock", self.now - timedelta(seconds=1))

    def test_recovery_cannot_use_existing_loop_clock_before_new_binding(self) -> None:
        old = self.begin()
        self.bind(old)
        self.ledger.begin_loop("task-clock", old, self.now)
        self.ledger.owner_stopped("task-clock", old, "containers-terminated")
        current = self.begin(10)
        with self.assertRaises(RecoveryConflict):
            self.ledger.begin_loop("task-clock", current, self.now + timedelta(seconds=11))
        with self.assertRaises(RecoveryConflict):
            self.ledger.reserve("task-clock", current, "model", self.now + timedelta(seconds=11))
