from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agentd.app.runtime.recovery import RecoveryConflict, RecoveryLedger, RecoveryLimit


class RecoveryLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="sandboxd-recovery-", dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "control" / "recovery.jsonl"
        self.now = datetime(2026, 10, 17, tzinfo=timezone.utc)
        self.ledger = RecoveryLedger(self.path)
        self.addCleanup(lambda: self.ledger.close())
        self.ledger.create(task_id="task-demo", session_id="session-demo", branch_id="branch-main",
                           workspace_id="workspace-demo", alert={"labels": {"cluster": "demo"}},
                           deadline=self.now + timedelta(seconds=120))

    def begin(self) -> str:
        return self.ledger.begin_attempt("task-demo", self.now)["attempts"][-1]["attemptId"]

    def reopen(self) -> None:
        self.ledger.close()
        self.ledger = RecoveryLedger(self.path)

    def test_restart_preserves_identity_deadline_and_reservations(self) -> None:
        attempt = self.begin()
        self.ledger.bind("task-demo", attempt, "sandbox-a", "pod-uid-a")
        self.ledger.select_provider("task-demo", "replay", "fixture")
        call = self.ledger.reserve("task-demo", attempt, "model", self.now)
        before = self.ledger.get("task-demo")
        self.reopen()
        self.assertEqual(self.ledger.get("task-demo"), before)
        with self.assertRaises(RecoveryConflict):
            self.begin()
        self.ledger.owner_stopped("task-demo", attempt, "containers-terminated")
        attempt2 = self.begin()
        current = self.ledger.get("task-demo")
        self.assertNotEqual(attempt, attempt2)
        self.assertEqual(current["calls"][call]["state"], "uncertain")
        self.assertEqual(current["deadline"], before["deadline"])
        self.assertEqual(current["provider"], before["provider"])
        for key in ("taskId", "sessionId", "branchId", "workspaceId"):
            self.assertEqual(current[key], before[key])

    def test_uncertain_reservation_consumes_budget(self) -> None:
        attempt = self.begin()
        for _ in range(6):
            self.ledger.reserve("task-demo", attempt, "model", self.now)
        self.ledger.owner_stopped("task-demo", attempt, "never-bound")
        attempt2 = self.begin()
        with self.assertRaises(RecoveryLimit):
            self.ledger.reserve("task-demo", attempt2, "model", self.now)
        self.assertEqual(len(self.ledger.get("task-demo")["calls"]), 6)

    def test_deadline_includes_downtime(self) -> None:
        self.reopen()
        later = self.now + timedelta(seconds=120)
        self.assertEqual(self.ledger.candidates(later), [])
        with self.assertRaises(RecoveryLimit):
            self.ledger.begin_attempt("task-demo", later)

    def test_recovery_is_bounded(self) -> None:
        for _ in range(3):
            attempt = self.begin()
            self.ledger.owner_stopped("task-demo", attempt, "never-bound")
        self.reopen()
        with self.assertRaises(RecoveryLimit):
            self.begin()
        self.assertEqual(self.ledger.candidates(self.now), [])

    def test_cancel_is_durable_and_precedes_rebind(self) -> None:
        attempt = self.begin()
        self.ledger.cancel("task-demo")
        self.reopen()
        with self.assertRaises(RecoveryConflict):
            self.ledger.bind("task-demo", attempt, "sandbox-b", "pod-uid-b")
        self.ledger.owner_stopped("task-demo", attempt, "never-bound")
        self.assertEqual(self.ledger.get("task-demo")["status"], "cancelled")
        with self.assertRaises(RecoveryConflict):
            self.begin()

    def test_terminal_tasks_do_not_resurrect(self) -> None:
        for status in ("succeeded", "failed", "limit_exceeded", "cancelled"):
            task = "task-" + status.replace("_", "-")
            self.ledger.create(task_id=task, session_id="session-test", branch_id="branch-test",
                               workspace_id="workspace-" + task, alert={}, deadline=self.now + timedelta(seconds=120))
            self.ledger.finish(task, status)
            self.reopen()
            with self.assertRaises(RecoveryConflict):
                self.ledger.begin_attempt(task, self.now)

    def test_commands_have_durable_ids_and_consumption_boundary(self) -> None:
        attempt = self.begin()
        a = self.ledger.queue_command("task-demo", "steer", "先核查 Pod")
        b = self.ledger.queue_command("task-demo", "follow-up", "再查 Service")
        self.ledger.complete_boundary("task-demo", attempt, "node-complete", [a])
        self.reopen()
        commands = self.ledger.get("task-demo")["commands"]
        self.assertEqual(commands[0]["appliedNodeId"], "node-complete")
        self.assertEqual(commands[1]["commandId"], b)
        self.assertIsNone(commands[1]["appliedNodeId"])
        with self.assertRaises(RecoveryConflict):
            self.ledger.complete_boundary("task-demo", attempt, "node-other", [a])
        with self.assertRaises(RecoveryConflict):
            self.ledger.complete_boundary("task-demo", attempt, "node-other", ["command-unknown"])

    def test_stale_attempt_and_rerouting_are_rejected(self) -> None:
        old = self.begin()
        self.ledger.select_provider("task-demo", "replay", "fixed")
        self.ledger.owner_stopped("task-demo", old, "never-bound")
        self.begin()
        with self.assertRaises(RecoveryConflict):
            self.ledger.reserve("task-demo", old, "tool", self.now)
        with self.assertRaises(RecoveryConflict):
            self.ledger.select_provider("task-demo", "other", "model")

    def test_workspace_ownership_is_not_just_task_identity(self) -> None:
        with self.assertRaises(RecoveryConflict):
            self.ledger.create(task_id="task-other", session_id="session-other", branch_id="branch-other",
                               workspace_id="workspace-demo", alert={}, deadline=self.now + timedelta(seconds=120))

    def test_failed_fsync_does_not_publish_state(self) -> None:
        before = self.ledger.get("task-demo")
        with patch("agentd.app.runtime.durable.os.fsync", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.ledger.cancel("task-demo")
        self.assertEqual(self.ledger.get("task-demo"), before)

    def test_redaction_and_unconfirmed_write_metadata(self) -> None:
        command = self.ledger.queue_command("task-demo", "steer", "api_key=canary-sensitive")
        self.assertTrue(command.startswith("command-"))
        attempt = self.begin()
        call = self.ledger.reserve("task-demo", attempt, "tool", self.now,
                                   operation="write_file", path="report.md", expected_new_sha256="a" * 64)
        self.ledger.owner_stopped("task-demo", attempt, "never-bound")
        state = self.ledger.get("task-demo")
        self.assertEqual(state["calls"][call]["state"], "uncertain")
        self.assertEqual(state["calls"][call]["expectedNewSha256"], "a" * 64)
        self.assertNotIn("canary-sensitive", self.path.read_text())
        self.assertNotIn("content", state["calls"][call])

    def test_termination_evidence_cannot_be_a_lease_or_deleted_object(self) -> None:
        attempt = self.begin()
        self.ledger.bind("task-demo", attempt, "sandbox-a", "pod-uid-a")
        for reason in ("lease-expired", "pod-object-deleted", "never-bound"):
            with self.assertRaises((ValueError, RecoveryConflict)):
                self.ledger.owner_stopped("task-demo", attempt, reason)
        with self.assertRaises(RecoveryConflict):
            self.ledger.finish("task-demo", "succeeded")
