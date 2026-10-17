from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from langchain_core.messages import AIMessage

from agentd.app.models import TraceStep
from agentd.app.model_gateway import LiveModelSession, ReplayModelSession
from agentd.app.runtime.call_audit import normalize_audit
from agentd.app.runtime.durable import JournalCorrupt
from agentd.app.runtime.recovery import RecoveryLedger


def model_audit():
    return {"kind": "model", "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
            "usageKnown": True, "elapsedMs": 20, "finishReason": "tool_calls"}


class CallAuditTests(unittest.TestCase):
    """固定时钟和合成计数；不是Provider计费或真实进程故障证据。"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "control/tasks.jsonl"
        self.ledger = RecoveryLedger(self.path)
        self.addCleanup(lambda: self.ledger.close())
        self.now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.ledger.create(task_id="task-a", session_id="session-a", branch_id="branch-a", workspace_id="workspace-a", alert={})
        self.attempt = self.ledger.begin_attempt("task-a", self.now)["attempts"][-1]["attemptId"]
        self.ledger.bind("task-a", self.attempt, "sandbox-a", "pod-a")
        self.ledger.begin_loop("task-a", self.attempt, self.now)

    def reserve(self, kind="model", **kwargs):
        return self.ledger.reserve("task-a", self.attempt, kind, self.now, **kwargs)

    def reopen(self):
        self.ledger.close()
        self.ledger = RecoveryLedger(self.path)

    def test_response_audit_survives_before_finish_without_refunding_reservation(self):
        call = self.reserve()
        self.ledger.record_audit("task-a", self.attempt, model_audit(), call)
        original = self.path.read_bytes()
        self.reopen()
        self.assertEqual(self.path.read_bytes(), original)
        self.ledger.owner_stopped("task-a", self.attempt, "containers-terminated")
        self.assertEqual(self.ledger.get("task-a")["calls"][call]["state"], "uncertain")
        trace = self.ledger.recovery_trace("task-a", self.attempt)
        self.assertEqual(trace.model_usage.total_tokens, 10)
        self.assertTrue(trace.events[0].details["usageKnown"])
        self.assertIn(call, trace.events[0].details["uncertainReservations"])

    def test_missing_inflight_usage_remains_unknown_and_trace_is_immutable(self):
        call = self.reserve()
        self.ledger.record_audit("task-a", self.attempt, model_audit(), call)
        self.ledger.finish_call("task-a", self.attempt, call)
        missing = self.reserve()
        self.ledger.owner_stopped("task-a", self.attempt, "containers-terminated")
        trace = self.ledger.recovery_trace("task-a", self.attempt)
        self.assertEqual(trace.model_usage.total_tokens, 10)
        self.assertFalse(trace.events[0].details["usageKnown"])
        self.assertEqual(trace.events[0].details["modelReservations"], 2)
        self.assertEqual(trace.events[0].details["modelResponsesRecorded"], 1)
        self.assertIn(missing, trace.events[0].details["uncertainReservations"])
        self.ledger.record_trace(trace)
        self.reopen()
        self.assertEqual(self.ledger.traces("task-a")[0]["modelUsage"]["totalTokens"], 10)

    def test_tool_evidence_is_bounded_redacted_and_json_preserved(self):
        call = self.reserve("tool", operation="write_file", path="draft.md")
        payload = {"body": {"api_key": "canary-secret", "output": "authorization=Bearer canary-token"}, "evidenceId": "ev-a"}
        step = TraceStep(index=1, node="execute_tool", tool="write_file", arguments={"path": "draft.md", "content": "private draft"},
                         observation=json.dumps(payload), auditDetails=payload)
        self.ledger.record_audit("task-a", self.attempt, {"kind": "tool", "step": step.model_dump(by_alias=True), "outcomeKnown": True}, call)
        self.ledger.finish_call("task-a", self.attempt, call)
        self.reopen()
        audit = self.ledger.audits("task-a", self.attempt)[0]["audit"]
        self.assertEqual(normalize_audit(audit), audit)
        raw = json.dumps(audit)
        for value in ("canary-secret", "canary-token", "private draft"):
            self.assertNotIn(value, raw)
        self.assertEqual(json.loads(audit["step"]["observation"])["evidenceId"], "ev-a")
        self.assertTrue(audit["step"]["arguments"]["content"]["redacted"])
        self.ledger.owner_stopped("task-a", self.attempt, "containers-terminated")
        self.assertEqual(self.ledger.recovery_trace("task-a", self.attempt).steps[0].tool, "write_file")

    def test_legacy_without_audits_is_readable_but_unknown(self):
        call = self.reserve()
        self.ledger.finish_call("task-a", self.attempt, call)
        self.reopen()
        self.ledger.owner_stopped("task-a", self.attempt, "containers-terminated")
        trace = self.ledger.recovery_trace("task-a", self.attempt)
        self.assertFalse(trace.events[0].details["usageKnown"])
        self.assertEqual(trace.events[0].details["modelResponsesRecorded"], 0)

    def test_duplicate_and_cross_scope_audits_fail_without_append(self):
        call = self.reserve()
        self.ledger.record_audit("task-a", self.attempt, model_audit(), call)
        before = self.path.read_bytes()
        for task, attempt, cid in (("task-a", self.attempt, call), ("task-other", self.attempt, call), ("task-a", "attempt-other", call)):
            with self.subTest(task=task, attempt=attempt), self.assertRaises(JournalCorrupt):
                self.ledger.record_audit(task, attempt, model_audit(), cid)
            self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_complete_audit_fails_startup(self):
        data = {"auditId": "audit-bad", "taskId": "task-a", "attemptId": self.attempt,
                "callId": "call-missing", "audit": model_audit()}
        self.ledger._journal.append("call.audit", data)
        self.ledger.close()
        with self.assertRaises(JournalCorrupt):
            RecoveryLedger(self.path)

    def test_unknown_usage_flag_and_invalid_counter_are_not_coerced(self):
        audit = model_audit()
        audit["usageKnown"] = False
        self.assertFalse(normalize_audit(audit)["usageKnown"])
        audit["usage"]["inputTokens"] = True
        with self.assertRaises(ValueError):
            normalize_audit(audit)

    def test_unreserved_denial_can_be_audited_only_once(self):
        step = TraceStep(index=1, node="execute_tool", tool="shell", denied=True, observation='{"denied":true}')
        audit = {"kind": "tool", "step": step.model_dump(by_alias=True), "outcomeKnown": True}
        self.ledger.record_audit("task-a", self.attempt, audit)
        with self.assertRaises(JournalCorrupt):
            self.ledger.record_audit("task-a", self.attempt, audit)

    def test_tool_audit_cannot_skip_step_order(self):
        step = TraceStep(index=2, node="execute_tool", tool="shell", denied=True)
        before = self.path.read_bytes()
        with self.assertRaises(JournalCorrupt):
            self.ledger.record_audit("task-a", self.attempt, {"kind": "tool", "step": step.model_dump(by_alias=True), "outcomeKnown": True})
        self.assertEqual(self.path.read_bytes(), before)


class ModelUsageProvenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_live_metadata_is_unknown_even_when_default_count_is_zero(self):
        class FakeProvider:
            async def ainvoke(self, messages):
                return AIMessage(content="synthetic")

        result = await LiveModelSession(FakeProvider(), []).invoke([])
        self.assertEqual(result.usage.total_tokens, 0)
        self.assertFalse(result.usage_known)

    async def test_recorded_provider_metadata_and_replay_zero_have_explicit_provenance(self):
        class FakeProvider:
            async def ainvoke(self, messages):
                return AIMessage(content="synthetic", usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})

        result = await LiveModelSession(FakeProvider(), []).invoke([])
        self.assertTrue(result.usage_known)
        self.assertEqual(result.usage.total_tokens, 10)
        replay = await ReplayModelSession([{"content": "synthetic"}]).invoke([])
        self.assertTrue(replay.usage_known)
        self.assertEqual(replay.usage.total_tokens, 0)
