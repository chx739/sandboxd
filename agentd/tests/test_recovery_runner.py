from __future__ import annotations

import asyncio
import copy
import hashlib
import gc
import json
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import httpx

from agentd.app.clients import HTTPResult
from agentd.app.model_gateway import ReplayModelGateway
from agentd.app.models import AlertEvent, utc_now
from agentd.app.runner import AgentRunner
from agentd.app.runtime.execution import RecoveryAttempt
from agentd.app.runtime.recovery import RecoveryConflict, RecoveryLedger, RecoveryLimit
from agentd.app.runtime.session import SessionJournal
from agentd.app.tools.remote_files import WorkspaceExecutionError
from agentd.app.router import ChoiceJudgement, ModelRouter
from agentd.tests.recovery_clock import install_fixed_recovery_clock


class FakeWorkspaceService:
    """明确为Fake：模拟可信Go响应，不证明真实Pod/NFS或进程故障验收。"""
    def __init__(self) -> None:
        self.record = None
        self.files = {}
        self.writes = []
        self.reads = []
        self.claims = 0
        self.lose_claim = False
        self.unknown_claim = False
        self.lose_write = False
        self.commit_write = True
        self.block_write = False
        self.entered = asyncio.Event()

    def result(self):
        return HTTPResult(200, copy.deepcopy(self.record))

    async def prepare_workspace(self, workspace, session, branch):
        if self.record is None:
            self.record = {"workspaceId": workspace, "sessionId": session, "branchId": branch,
                           "phase": "ready", "generation": 0, "owner": {}}
        return self.result()

    async def claim_workspace(self, workspace, task, attempt):
        self.claims += 1
        if self.unknown_claim:
            raise httpx.ReadTimeout("synthetic unconfirmed claim")
        self.record.update({"phase": "bound", "generation": self.claims, "owner": {
            "taskId": task, "attemptId": attempt, "sandboxId": "ws-" + str(self.claims),
            "podUid": "12345678-1234-4321-8123-" + str(self.claims).zfill(12), "stopConfirmed": False,
        }})
        if self.lose_claim:
            self.lose_claim = False
            raise httpx.ReadTimeout("synthetic lost claim response")
        return self.result()

    async def get_workspace(self, workspace):
        return self.result()

    async def release_workspace(self, binding):
        if self.record["owner"]["attemptId"] != binding["attemptId"]:
            raise AssertionError("cleanup attempted another owner")
        self.record["phase"] = "released"
        self.record["owner"]["stopConfirmed"] = True
        self.record["stopReason"] = "containers-terminated"
        return self.result()

    async def workspace_files(self, binding, operation):
        if self.record["phase"] != "bound" or binding["attemptId"] != self.record["owner"]["attemptId"]:
            raise AssertionError("stale file request")
        path = operation["path"]
        if operation["operation"] == "write_file":
            self.writes.append((binding["attemptId"], path))
            if self.commit_write:
                self.files[path] = operation["content"]
            self.entered.set()
            if self.block_write:
                await asyncio.Event().wait()
            if self.lose_write:
                self.lose_write = False
                raise httpx.ReadTimeout("synthetic lost write response")
            return HTTPResult(200, {"sha256": hashlib.sha256(self.files[path].encode()).hexdigest()})
        if operation["operation"] == "read_file":
            self.reads.append((binding["attemptId"], path))
            if path not in self.files:
                return HTTPResult(404, {"error": "synthetic file missing"})
            content = self.files[path]
            return HTTPResult(200, {"content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()})
        raise AssertionError("unexpected file operation")


class RecoveryRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        install_fixed_recovery_clock(self)
        self.tmp = tempfile.TemporaryDirectory(prefix="sandboxd-runner-recovery-", dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "control" / "tasks.jsonl"
        self.ledger = RecoveryLedger(self.path)
        self.addCleanup(lambda: self.ledger.close())
        self.alert = AlertEvent(annotations={"summary": "synthetic recovery"})
        self.ledger.create(task_id="task-demo", session_id="session-0123456789abcdef", branch_id="branch-main",
                           workspace_id="workspace-" + "a" * 32, alert=self.alert.model_dump(mode="json", by_alias=True))
        self.journal = SessionJournal(self.root / "sessions", "session-0123456789abcdef")
        await self.journal.initialize("task-demo", self.alert)
        self.script = self.root / "replay.json"
        self.script.write_text(json.dumps({"kind": "deterministic-policy-case", "model": "synthetic-recovery", "responses": [
            {"toolCalls": [{"id": "write-1", "name": "write_file", "args": {"path": "report.md", "content": "draft"}}]},
            {"content": '{"summary":"done","severity":"warning","recommendation":"observe"}'},
        ]}))
        self.client = FakeWorkspaceService()
        self.gateway = ReplayModelGateway(self.script)
        self.runner = AgentRunner(object(), self.client, self.gateway, workspace_root=self.root / "must-not-exist")

    def attempt(self):
        state = self.ledger.begin_attempt("task-demo", utc_now())
        return RecoveryAttempt(self.ledger, "task-demo", state["attempts"][-1]["attemptId"])

    async def run_attempt(self, attempt):
        return await self.runner.run("task-demo", self.alert, journal=self.journal, recovery=attempt)

    async def test_runner_injects_remote_workspace_and_commits_budgets_and_trace(self):
        attempt = self.attempt()
        result, trace, status = await self.run_attempt(attempt)
        self.assertEqual(status, "succeeded")
        self.assertEqual(result.summary, "done")
        self.assertFalse((self.root / "must-not-exist").exists())
        self.assertEqual((attempt.used("model"), attempt.used("tool")), (2, 1))
        state = attempt.snapshot()
        self.assertIsNotNone(state["timing"]["loopStartedAt"])
        self.assertIsNotNone(state["completeNodeId"])
        self.assertTrue(state["attempts"][-1]["ownerStopped"])
        self.assertEqual(self.client.files["report.md"], "draft")
        self.assertEqual(trace.attempt_id, attempt.attempt_id)
        self.assertEqual(len(self.ledger.traces("task-demo")), 1)
        with self.assertRaises(RecoveryConflict):
            self.ledger.record_trace(trace.model_copy(update={"verdict": "changed"}))

    async def test_budget_preflight_failure_does_not_leak_unawaited_claim(self):
        attempt = self.attempt()
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always", RuntimeWarning)
            with patch.object(attempt, "remaining", side_effect=RecoveryConflict("synthetic clock rollback")):
                with self.assertRaisesRegex(RecoveryConflict, "synthetic clock rollback"):
                    await self.run_attempt(attempt)
            gc.collect()
        self.assertFalse(any("was never awaited" in str(w.message) for w in observed))
        self.assertEqual(self.client.claims, 0)
        self.assertTrue(attempt.snapshot()["attempts"][-1]["ownerStopped"])

    async def test_lost_write_response_is_inspected_after_reopen_and_not_replayed(self):
        first = self.attempt()
        self.client.lose_write = True
        with self.assertRaises(WorkspaceExecutionError):
            await self.run_attempt(first)
        original_deadline = first.snapshot()["deadline"]
        self.assertEqual(first.used("model"), 1)
        self.ledger.close()
        self.ledger = RecoveryLedger(self.path)
        second = self.attempt()
        result, trace, status = await self.run_attempt(second)
        self.assertEqual(status, "succeeded")
        self.assertEqual(result.summary, "done")
        self.assertEqual(len(self.client.writes), 1)
        self.assertEqual(len(self.client.reads), 1)
        self.assertEqual(second.used("model"), 2)
        self.assertEqual(second.snapshot()["deadline"], original_deadline)
        self.assertNotEqual(first.attempt_id, trace.attempt_id)
        self.assertEqual(len(self.ledger.traces("task-demo")), 2)
        self.assertTrue(any(e.type == "workspace.write.inspected" for e in trace.events))
        self.assertEqual(trace.steps[0].plugin_id, "workspace-recovery")
        self.assertTrue(json.loads(trace.steps[0].observation)["matchesExpectedWrite"])
        inspections = [a for a in self.ledger.audits("task-demo", second.attempt_id)
                       if a["audit"]["kind"] == "tool" and a["audit"]["step"]["pluginId"] == "workspace-recovery"]
        self.assertEqual(len(inspections), 1)
        self.assertEqual(inspections[0]["audit"]["step"]["tool"], "read_file")

    async def test_unknown_write_without_matching_hash_stops_before_another_model_call(self):
        self.client.lose_write, self.client.commit_write = True, False
        with self.assertRaises(WorkspaceExecutionError):
            await self.run_attempt(self.attempt())
        second = self.attempt()
        with self.assertRaises(WorkspaceExecutionError) as caught:
            await self.run_attempt(second)
        self.assertFalse(caught.exception.recoverable)
        self.assertEqual(second.used("model"), 1)
        self.assertEqual(len(self.client.writes), 1)
        self.assertTrue(second.snapshot()["attempts"][-1]["ownerStopped"])

    async def test_lost_claim_response_reconciles_go_owner_instead_of_assuming_absence(self):
        self.client.lose_claim = True
        attempt = self.attempt()
        with self.assertRaises(httpx.ReadTimeout):
            await self.run_attempt(attempt)
        owner = attempt.snapshot()["attempts"][-1]
        self.assertTrue(owner["ownerStopped"])
        self.assertIsNotNone(owner["podUid"])
        self.assertEqual(self.client.record["phase"], "released")
        self.assertEqual(self.ledger.traces("task-demo")[0]["sandboxId"], owner["sandboxId"])

    async def test_absent_claim_binding_blocks_takeover(self):
        self.client.unknown_claim = True
        attempt = self.attempt()
        with self.assertRaises(RecoveryConflict):
            await self.run_attempt(attempt)
        self.assertFalse(attempt.snapshot()["attempts"][-1]["ownerStopped"])
        with self.assertRaises(RecoveryConflict):
            self.attempt()
        self.assertEqual(len(self.client.writes), 0)

    async def test_changed_replay_fixture_is_rejected_before_claim_or_model(self):
        self.client.lose_write = True
        with self.assertRaises(WorkspaceExecutionError):
            await self.run_attempt(self.attempt())
        self.script.write_text(self.script.read_text().replace("done", "different"))
        self.runner = AgentRunner(object(), self.client, ReplayModelGateway(self.script))
        second = self.attempt()
        with self.assertRaises(RecoveryConflict):
            await self.run_attempt(second)
        self.assertEqual(self.client.claims, 1)
        self.assertEqual(second.used("model"), 1)
        self.assertTrue(second.snapshot()["attempts"][-1]["ownerStopped"])

    async def test_cancellation_is_preserved_while_resource_cleanup_records_binding(self):
        attempt = self.attempt()
        self.client.block_write = True
        task = asyncio.create_task(self.run_attempt(attempt))
        await asyncio.wait_for(self.client.entered.wait(), 3)
        self.ledger.cancel("task-demo")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(attempt.snapshot()["status"], "cancelled")
        self.assertTrue(attempt.snapshot()["attempts"][-1]["ownerStopped"])
        self.assertEqual(attempt.used("model"), 1)
        self.assertEqual(len(self.ledger.traces("task-demo")), 1)

    async def test_router_is_not_called_again_after_restart(self):
        class CountingSource:
            source = "deterministic-fake"
            calls = 0
            async def classify(self, summary):
                self.calls += 1
                return ChoiceJudgement("economy", 0.9)
        source = CountingSource()
        router = ModelRouter({"economy": self.gateway, "strong": self.gateway}, source)
        self.runner = AgentRunner(object(), self.client, self.gateway, model_router=router)
        self.client.lose_write = True
        with self.assertRaises(WorkspaceExecutionError):
            await self.run_attempt(self.attempt())
        self.ledger.close()
        self.ledger = RecoveryLedger(self.path)
        _, trace, status = await self.run_attempt(self.attempt())
        self.assertEqual(status, "succeeded")
        self.assertEqual(source.calls, 1)
        self.assertTrue(trace.routing["restored"])
        self.assertEqual(self.ledger.get("task-demo")["provider"]["tier"], "economy")

    async def test_unconfirmed_router_reservation_uses_fixed_fallback_without_another_request(self):
        first = self.attempt()
        first.reserve("router")
        self.ledger.owner_stopped("task-demo", first.attempt_id, "never-bound")
        class MustNotClassify:
            source = "deterministic-fake"
            async def classify(self, summary):
                raise AssertionError("routing request must not be repeated")
        router = ModelRouter({"economy": self.gateway, "strong": self.gateway}, MustNotClassify())
        self.runner = AgentRunner(object(), self.client, self.gateway, model_router=router)
        _, trace, status = await self.run_attempt(self.attempt())
        self.assertEqual(status, "succeeded")
        self.assertEqual(trace.routing["effectiveTier"], "strong")
        self.assertEqual(trace.routing["fallbackReason"], "router_result_unknown")
        self.assertEqual(sum(c["kind"] == "router" for c in self.ledger.get("task-demo")["calls"].values()), 1)

    async def test_exhausted_model_reservations_stop_before_claiming_another_pod(self):
        first = self.attempt()
        await self.runner._claim_workspace(first)
        self.ledger.begin_loop("task-demo", first.attempt_id, utc_now())
        for _ in range(6):
            first.reserve("model")
        await self.runner.stop_attempt(first)
        second = self.attempt()
        with self.assertRaises(RecoveryLimit):
            await self.run_attempt(second)
        self.assertEqual(second.used("model"), 6)
        self.assertEqual(self.client.claims, 1)
