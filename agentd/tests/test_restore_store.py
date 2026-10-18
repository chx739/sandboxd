"""合成Go/restic + 实际TaskStore/Runner，不冒充真实服务或SIGKILL。"""
from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

from agentd.app.clients import HTTPResult
from agentd.app.models import AlertEvent, utc_now
from agentd.app.model_gateway import ReplayModelGateway
from agentd.app.runner import AgentRunner
from agentd.app.store import TaskStore, TaskConflictError
from agentd.app.runtime.checkpoints import CheckpointLedger
from agentd.app.runtime.checkpoint_coordinator import CheckpointCoordinator
from agentd.app.runtime.execution import RecoveryAttempt
from agentd.app.runtime.recovery import new_id
from agentd.app.runtime.session_refs import SessionReferences
from agentd.tests.recovery_clock import install_fixed_recovery_clock, FixedRecoveryClock
from agentd.tests.test_checkpoint_coordinator import FakeBackupService


class FakeRestoreService(FakeBackupService):
    def __init__(self):
        super().__init__()
        self.restore_barrier = None
        self.restore_actions = []
        self.restore_entered = asyncio.Event()
        self.block_restore = False
        self.restore_result_wrong = False

    async def get_workspace_restore(self, workspace):
        return HTTPResult(404, {}) if self.restore_barrier is None else HTTPResult(200, copy.deepcopy(self.restore_barrier))

    async def get_workspace_backup(self, workspace):
        if self.barrier is None and self.restore_barrier is not None:
            return HTTPResult(200, copy.deepcopy(self.restore_barrier))
        return await super().get_workspace_backup(workspace)

    async def workspace_restore(self, action, target, cp, job, source, *, timeout=90):
        self.restore_actions.append((action, job, timeout))
        if action == "reserve":
            self.restore_barrier = {"workspaceId": target["workspaceId"], "checkpointId": cp, "jobId": job,
                                    "operation": "restore", "restoreSource": copy.deepcopy(source), "phase": "reserved"}
        elif action == "start":
            self.restore_barrier["phase"] = "identified"
        elif action == "wait":
            self.restore_entered.set()
            if self.block_restore:
                await asyncio.Event().wait()
            self.files = {"report.md": "source version"}
            self.restore_barrier.update(phase="stopped", stopReason="containers-terminated")
            result = {k: source[k] for k in ("snapshotId", "manifestSha256", "repositoryCryptoId")}
            if self.restore_result_wrong:
                result["manifestSha256"] = "f" * 64
            return HTTPResult(200, {"barrier": copy.deepcopy(self.restore_barrier), "result": result})
        elif action == "abort":
            if self.unknown_stop:
                raise TimeoutError("synthetic unknown maintenance termination")
            self.restore_barrier.update(phase="stopped", stopReason="containers-terminated")
        elif action == "finish":
            self.restore_barrier.update(phase="released", restoreVerified=True)
            self.record["restoredFrom"] = copy.deepcopy(source)
        elif action == "fail":
            self.restore_barrier.update(phase="released", restoreVerified=False)
        return HTTPResult(200, {"barrier": copy.deepcopy(self.restore_barrier)})

    async def claim_workspace(self, *args):
        assert self.restore_barrier["phase"] == "released" and self.restore_barrier["restoreVerified"]
        return await super().claim_workspace(*args)


class RestoreStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        install_fixed_recovery_clock(self)
        for name in ("agentd.app.runtime.checkpoints.datetime", "agentd.app.runtime.checkpoint_coordinator.datetime"):
            patcher = patch(name, FixedRecoveryClock);patcher.start();self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp", prefix="sandboxd-restore-store-");self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name);self.control = self.root / "control"
        self.store = TaskStore(self.root / "legacy", recovery_dir=self.control)
        self.addCleanup(lambda: self.store.close())
        self.refs = SessionReferences(self.control / "checkpoints")
        self.ledger = CheckpointLedger(self.control / "checkpoints/checkpoints.jsonl", self.refs)
        self.addCleanup(self.ledger.close)
        self.client = FakeRestoreService()
        self.coordinator = CheckpointCoordinator(self.client, self.ledger, self.refs, "repo-fixture")
        source = await self.store.enqueue(AlertEvent(labels={"cluster": "synthetic"}))
        self.sid = source.session_id;self.source_task = source.task_id
        journal = self.store._journal(self.sid)
        messages = [SystemMessage(content="safety"), HumanMessage(content="incident"),
                    AIMessage(content="", tool_calls=[{"id": "old-write", "name": "write_file", "args": {"path": "report.md", "content": "source version"}}]),
                    ToolMessage(content="old observation", tool_call_id="old-write")]
        self.node = await journal.append_transcript(source.task_id, messages)
        source_attempt = self.store._ledger.begin_attempt(source.task_id, utc_now())["attempts"][-1]["attemptId"]
        self.cp = new_id("cp")
        identity = {"sessionId": self.sid, "branchId": source.branch_id, "workspaceId": source.workspace_id,
                    "sessionNodeId": self.node, "taskId": source.task_id, "attemptId": source_attempt,
                    "sandboxId": "source-sandbox", "podUid": "source-pod", "generation": 1,
                    "imageRef": "sandboxd-workspace:runtime-v1.5", "imageId": "sha256:" + "4" * 64,
                    "sessionRef": await self.refs.capture(journal, self.node)}
        self.ledger.prepare(self.cp, identity, None)
        self.ledger.data_ready(self.cp, {"repositoryId": "repo-fixture", "repositoryCryptoId": "1" * 64,
                                      "snapshotId": "2" * 64, "manifestSha256": "3" * 64})
        self.ledger.commit(self.cp)
        self.latest_node = await journal.append_transcript(source.task_id, messages + [AIMessage(content="latest original context")])
        self.store._ledger.complete_boundary(source.task_id, source_attempt, self.latest_node, [])
        self.store._ledger.owner_stopped(source.task_id, source_attempt, "never-bound")
        await self.store._finish_durable(source.task_id, "succeeded")
        self.source_state = self.store._ledger.get(source.task_id)
        self.source_prefix = await journal.checkpoint_prefix(self.node)
        self.store.configure_checkpoints(self.coordinator)
        self.script = self.root / "replay.json"
        self.script.write_text(json.dumps({"kind": "deterministic-policy-case", "responses": [
            {"toolCalls": [{"id": "new-read", "name": "read_file", "args": {"path": "report.md"}}]},
            {"content": '{"summary":"restored","recommendation":"observe"}'}]}))
        self.runner = AgentRunner(object(), self.client, ReplayModelGateway(self.script), checkpoints=self.coordinator)
        self.workers = []
        self.addAsyncCleanup(self.stop_workers)

    async def stop_workers(self):
        for worker in self.workers:worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def worker(self):
        self.workers.append(asyncio.create_task(self.store.worker(self.runner)))

    async def settled(self, task):
        async with asyncio.timeout(8):
            while True:
                result = await self.store.get(task.task_id)
                if result.status in {"succeeded", "failed", "cancelled", "interrupted", "limit_exceeded"}:return result
                await asyncio.sleep(.01)

    def reopen(self):
        self.store.close()
        self.store = TaskStore(self.root / "legacy", recovery_dir=self.control)
        self.store.configure_checkpoints(self.coordinator)

    async def test_restore_runs_new_task_exact_prefix_parent_and_no_old_write(self):
        rid = new_id("restore")
        task = await self.store.restore_checkpoint(self.sid, self.cp, rid)
        self.assertNotEqual(task.workspace_id, self.source_state["workspaceId"])
        self.assertEqual((await self.store._journal(self.sid).summary())["activeLeafId"], self.latest_node)
        self.assertEqual((await self.store.restore_checkpoint(self.sid, self.cp, rid)).task_id, task.task_id)
        await self.worker()
        result = await self.settled(task)
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(self.store.restore_progress(self.sid, rid)["status"], "ATTACHED")
        self.assertEqual(self.client.writes, [])
        self.assertEqual(len(self.client.reads), 1)
        head = self.ledger.head(self.sid, result.branch_id)
        self.assertEqual(head["revision"], 2)
        first = self.ledger.get(head["parentCheckpointId"])
        self.assertEqual(first["parentCheckpointId"], self.cp)
        self.assertEqual(first["revision"], 1)
        self.assertEqual(self.store._ledger.get(self.source_task), self.source_state)
        self.assertEqual(await self.store._journal(self.sid).checkpoint_prefix(self.node), self.source_prefix)

    async def test_cancel_during_restore_stops_before_claim_or_model(self):
        self.client.block_restore = True
        task = await self.store.restore_checkpoint(self.sid, self.cp)
        await self.worker()
        await asyncio.wait_for(self.client.restore_entered.wait(), 3)
        await self.store.cancel(task.task_id)
        result = await self.settled(task)
        self.assertEqual(result.status, "cancelled", result.error)
        self.assertEqual(self.client.claims, 0)
        state = self.store._ledger.get(task.task_id)
        self.assertEqual(state["calls"], {})
        self.assertTrue(state["attempts"][-1]["ownerStopped"])
        request = self.store._restorer.for_task(task.task_id)
        self.assertEqual(request["status"], "FAILED")
        self.assertEqual(self.client.restore_barrier["phase"], "released")
        self.assertEqual((await self.store._journal(self.sid).summary())["activeLeafId"], self.latest_node)

    async def test_files_ready_restart_reuses_job_and_original_claim_deadline(self):
        task = await self.store.restore_checkpoint(self.sid, self.cp)
        state = self.store._ledger.begin_attempt(task.task_id, utc_now())
        recovery = RecoveryAttempt(self.store._ledger, task.task_id, state["attempts"][-1]["attemptId"])
        await self.store._restorer.ensure_files(self.store._restorer.for_task(task.task_id), recovery)
        before = recovery.snapshot()
        original_run = self.runner.run
        at_run = []
        async def capture(*args, **kwargs):
            at_run.append(kwargs["recovery"].snapshot())
            return await original_run(*args, **kwargs)
        self.runner.run = capture
        self.reopen()
        await self.worker()
        result = await self.settled(task)
        self.assertEqual(result.status, "succeeded", result.error)
        state = self.store._ledger.get(task.task_id)
        self.assertEqual(len(state["attempts"]), 2)
        self.assertEqual(state["timing"]["claimStartedAt"], before["timing"]["claimStartedAt"])
        self.assertEqual([a[0] for a in self.client.restore_actions].count("wait"), 1)
        self.assertEqual(state["limits"], before["limits"])
        self.assertEqual(at_run[0]["deadline"], before["deadline"])
        self.assertEqual(at_run[0]["calls"], before["calls"])

    async def test_reserved_orphan_task_is_adopted_and_resume_rejects_missing_snapshot(self):
        rid = new_id("restore")
        request = self.ledger.prepare_restore(rid, self.cp, session_id=self.sid, branch_id=new_id("branch"),
                                             workspace_id=new_id("workspace"), task_id="task-" + "a" * 16)
        self.reopen()
        task = await self.store.get(request["taskId"])
        self.assertEqual(task.status, "queued")
        with self.assertRaises(TaskConflictError):await self.store.resume(self.sid, "node-" + "f" * 16)
        await self.worker()
        self.assertEqual((await self.settled(task)).status, "succeeded")

    async def test_wrong_restore_result_fails_without_changing_source(self):
        self.client.restore_result_wrong = True
        task = await self.store.restore_checkpoint(self.sid, self.cp)
        with self.assertRaises(TaskConflictError):await self.store.resume(self.sid)
        await self.worker()
        self.assertEqual((await self.settled(task)).status, "failed")
        self.assertEqual(self.client.claims, 0)
        self.assertFalse(self.client.restore_barrier["restoreVerified"])
        self.assertEqual(self.store._ledger.get(self.source_task), self.source_state)
        await self.stop_workers()
        continued = await self.store.resume(self.sid)
        self.assertEqual(continued.workspace_id, self.source_state["workspaceId"])

    async def test_failed_attachment_does_not_route_latest_resume_to_old_context_or_failed_target(self):
        original = self.ledger.advance_restore
        def fail_attach(rid, status, failure=None):
            if status == "ATTACHED":raise RuntimeError("synthetic failure after Session selection")
            return original(rid, status, failure)
        with patch.object(self.ledger, "advance_restore", side_effect=fail_attach):
            task = await self.store.restore_checkpoint(self.sid, self.cp)
            await self.worker()
            self.assertEqual((await self.settled(task)).status, "failed")
        self.assertEqual((await self.store._journal(self.sid).summary())["activeLeafId"], self.node)
        summary = await self.store.get_session(self.sid)
        self.assertEqual(summary["activeLeafId"], self.latest_node)
        self.assertEqual(summary["workspaceId"], self.source_state["workspaceId"])
        self.assertEqual((await self.store.get_session_working_memory(self.sid))["nodeId"], self.latest_node)
        await self.stop_workers()
        continued = await self.store.resume(self.sid)
        self.assertEqual(continued.workspace_id, self.source_state["workspaceId"])
        self.assertEqual((await self.store._journal(self.sid).summary())["activeLeafId"], self.latest_node)

    async def test_unknown_restore_stop_blocks_takeover_until_cancel_reconciles(self):
        self.client.block_restore = True;self.client.unknown_stop = True
        task = await self.store.restore_checkpoint(self.sid, self.cp)
        await self.worker();await asyncio.wait_for(self.client.restore_entered.wait(), 3)
        await self.store.cancel(task.task_id)
        result = await self.settled(task)
        self.assertEqual(result.status, "interrupted")
        state = self.store._ledger.get(task.task_id)
        self.assertFalse(state["attempts"][-1]["ownerStopped"])
        self.assertEqual(len(state["attempts"]), 1)
        self.assertEqual(self.client.claims, 0)
        self.client.unknown_stop = False
        await self.store.cancel(task.task_id)
        self.assertEqual((await self.settled(task)).status, "cancelled")
        self.assertTrue(self.store._ledger.get(task.task_id)["attempts"][-1]["ownerStopped"])

    async def test_queued_restore_cancel_rebuilds_failed_without_any_rpc(self):
        task = await self.store.restore_checkpoint(self.sid, self.cp)
        await self.store.cancel(task.task_id)
        self.reopen()
        self.assertEqual(self.store._restorer.for_task(task.task_id)["status"], "FAILED")
        self.assertEqual(self.client.restore_actions, [])

    async def test_cancel_after_restart_stays_cancelling_until_old_restore_is_stopped(self):
        task = await self.store.restore_checkpoint(self.sid, self.cp)
        state = self.store._ledger.begin_attempt(task.task_id, utc_now())
        recovery = RecoveryAttempt(self.store._ledger, task.task_id, state["attempts"][-1]["attemptId"])
        await self.store._restorer.ensure_files(self.store.restore_for_task(task.task_id), recovery)
        self.reopen()
        result = await self.store.cancel(task.task_id)
        self.assertEqual(result.status, "cancelling")
        await self.worker()
        result = await self.settled(task)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.client.claims, 0)
        self.assertTrue(self.store._ledger.get(task.task_id)["attempts"][-1]["ownerStopped"])

    async def test_node_branch_selects_unique_checkpoint_and_rejects_ambiguity(self):
        task = await self.store.resume(self.sid, self.node)
        self.assertEqual(self.store.restore_for_task(task.task_id)["sourceCheckpointId"], self.cp)
        await self.store.cancel(task.task_id)
        duplicate = new_id("cp")
        identity = dict(self.ledger.get(self.cp)["identity"], attemptId=new_id("attempt"))
        self.ledger.prepare(duplicate, identity, self.cp)
        self.ledger.data_ready(duplicate, self.ledger.get(self.cp)["data"])
        self.ledger.commit(duplicate)
        with self.assertRaises(TaskConflictError):await self.store.resume(self.sid, self.node)

    async def test_attached_task_recovery_keeps_latest_files_instead_of_restoring_again(self):
        self.script.write_text(json.dumps({"kind": "deterministic-policy-case", "responses": [
            {"toolCalls": [{"id": "new-write", "name": "write_file", "args": {"path": "report.md", "content": "new version"}}]},
            {"content": '{"summary":"latest state preserved","recommendation":"observe"}'}]}))
        self.runner = AgentRunner(object(), self.client, ReplayModelGateway(self.script), checkpoints=self.coordinator)
        self.client.lose_write = True
        task = await self.store.restore_checkpoint(self.sid, self.cp)
        await self.worker()
        result = await self.settled(task)
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(self.client.files["report.md"], "new version")
        self.assertEqual(len(self.client.writes), 1)
        self.assertEqual([a[0] for a in self.client.restore_actions].count("wait"), 1)
