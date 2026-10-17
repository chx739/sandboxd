from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from agentd.app.models import AlertEvent
from agentd.app.model_gateway import ReplayModelGateway
from agentd.app.runner import AgentRunner
from agentd.app.store import TaskStore, TaskConflictError
from agentd.tests.test_recovery_runner import FakeWorkspaceService
from agentd.tests.recovery_clock import install_fixed_recovery_clock


class DurableTaskStoreTests(unittest.IsolatedAsyncioTestCase):
    """真实TaskStore/Runner + Fake Go及固定时钟；不冒称真实NFS或SIGKILL验收。"""
    async def asyncSetUp(self):
        install_fixed_recovery_clock(self)
        self.tmp = tempfile.TemporaryDirectory(prefix="sandboxd-store-recovery-", dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.control = self.root / "control"
        self.store = TaskStore(self.root / "legacy-traces", recovery_dir=self.control)
        self.addCleanup(lambda: self.store.close())
        self.script = self.root / "replay.json"
        self.script.write_text(json.dumps({"kind": "deterministic-policy-case", "responses": [
            {"toolCalls": [{"id": "write-one", "name": "write_file", "args": {"path": "report.md", "content": "draft"}}]},
            {"content": '{"summary":"done","recommendation":"observe"}'},
            {"content": '{"summary":"followed","recommendation":"observe"}'},
        ]}))
        self.client = FakeWorkspaceService()
        self.runner = AgentRunner(object(), self.client, ReplayModelGateway(self.script), workspace_root=self.root / "no-local-files")
        self.alert = AlertEvent(annotations={"summary": "synthetic durable task"})

    async def worker(self):
        worker = asyncio.create_task(self.store.worker(self.runner))
        async def stop():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self.addAsyncCleanup(stop)
        return worker

    async def settled(self, task_id):
        async with asyncio.timeout(5):
            while True:
                task = await self.store.get(task_id)
                if task.status in {"succeeded", "failed", "cancelled", "limit_exceeded", "interrupted"}:
                    return task
                await asyncio.sleep(0.01)

    def reopen(self):
        self.store.close()
        self.store = TaskStore(self.root / "legacy-traces", recovery_dir=self.control)

    async def test_accepted_queued_task_is_automatically_discovered_after_reopen(self):
        task = await self.store.enqueue(self.alert)
        self.reopen()
        await self.worker()
        result = await self.settled(task.task_id)
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(result.workspace_id, task.workspace_id)
        self.assertEqual(result.branch_id, task.branch_id)
        self.assertEqual(result.created_at, task.created_at)
        self.assertEqual(result.attempt_count, 1)
        self.assertFalse((self.root / "no-local-files").exists())

    async def test_worker_automatically_continues_same_task_after_unknown_write(self):
        task = await self.store.enqueue(self.alert)
        self.client.lose_write = True
        await self.worker()
        result = await self.settled(task.task_id)
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.workspace_id, task.workspace_id)
        self.assertEqual(len(self.client.writes), 1)
        self.assertEqual(len(self.client.reads), 1)
        traces = await self.store.get_task_traces(task.task_id)
        self.assertEqual([t["status"] for t in traces], ["interrupted", "succeeded"])
        self.assertNotEqual(traces[0]["attemptId"], traces[1]["attemptId"])

    async def test_terminal_tasks_are_not_restarted(self):
        task = await self.store.enqueue(self.alert)
        worker = await self.worker()
        self.assertEqual((await self.settled(task.task_id)).status, "succeeded")
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        self.reopen()
        claims_before = self.client.claims
        await self.worker()
        await asyncio.sleep(0.03)
        restored = await self.store.get(task.task_id)
        self.assertEqual(restored.status, "succeeded")
        self.assertEqual(self.client.claims, claims_before)
        self.assertEqual(restored.result.summary, "done")

    async def test_queued_cancellation_is_persisted_before_restart(self):
        task = await self.store.enqueue(self.alert)
        await self.store.cancel(task.task_id)
        self.reopen()
        await self.worker()
        await asyncio.sleep(0.03)
        self.assertEqual((await self.store.get(task.task_id)).status, "cancelled")
        self.assertEqual(self.client.claims, 0)

    async def test_repeated_cancel_does_not_interrupt_inflight_owner_cleanup(self):
        task = await self.store.enqueue(self.alert)
        self.client.block_write = True
        cleanup_entered, cleanup_gate = asyncio.Event(), asyncio.Event()
        release = self.client.release_workspace

        async def gated_release(binding):
            cleanup_entered.set()
            await cleanup_gate.wait()
            return await release(binding)

        self.client.release_workspace = gated_release
        await self.worker()
        await asyncio.wait_for(self.client.entered.wait(), 3)
        try:
            first = await self.store.cancel(task.task_id)
            self.assertEqual(first.status, "cancelling")
            await asyncio.wait_for(cleanup_entered.wait(), 3)
            running = self.store._running[task.task_id]
            cancellations = running.cancelling()
            second = await self.store.cancel(task.task_id)
            self.assertEqual(second.status, "cancelling")
            self.assertEqual(running.cancelling(), cancellations, "duplicate request interrupted shielded cleanup")
            cleanup_gate.set()
            result = await self.settled(task.task_id)
            self.assertEqual(result.status, "cancelled", result.error)
            self.assertTrue(self.store._ledger.get(task.task_id)["attempts"][-1]["ownerStopped"])
            rows = [json.loads(line) for line in self.store._journals[task.task_id].path.read_text().splitlines()]
            self.assertEqual(sum(row["type"] == "session.command" and row.get("command") == "cancel" for row in rows), 1)
        finally:
            cleanup_gate.set()

    async def test_acknowledged_commands_survive_reopen_and_have_complete_boundaries(self):
        task = await self.store.enqueue(self.alert)
        await self.store.send_control(task.task_id, "steer", "only inspect")
        await self.store.send_control(task.task_id, "follow-up", "explain evidence")
        ids_before = [c["commandId"] for c in self.store._ledger.get(task.task_id)["commands"]]
        self.reopen()
        await self.worker()
        result = await self.settled(task.task_id)
        self.assertEqual(result.status, "succeeded", result.error)
        commands = self.store._ledger.get(task.task_id)["commands"]
        self.assertEqual([c["commandId"] for c in commands], ids_before)
        self.assertTrue(all(c["appliedNodeId"] is not None for c in commands))
        entries = [json.loads(line) for line in self.store._journals[task.task_id].path.read_text().splitlines()]
        messages = [e["message"] for e in entries if e["type"] == "session.node"]
        for command_id in ids_before:
            self.assertEqual(sum(m.get("commandId") == command_id for m in messages), 1)

    async def test_session_fsync_before_command_cursor_is_reconciled_without_reinjection(self):
        task = await self.store.enqueue(self.alert)
        await self.store.send_control(task.task_id, "steer", "only inspect")
        await self.store.send_control(task.task_id, "follow-up", "explain evidence")
        ids = [c["commandId"] for c in self.store._ledger.get(task.task_id)["commands"]]
        original = self.store._ledger.complete_boundary
        interrupted = []

        def boundary(task_id, attempt_id, node_id, command_ids):
            if command_ids and not interrupted:
                # Session已经fsync，此处显式中断，模拟跨日志提交窗口；不是SIGKILL证据。
                interrupted.append(node_id)
                raise asyncio.CancelledError
            return original(task_id, attempt_id, node_id, command_ids)

        self.store._ledger.complete_boundary = boundary
        worker = await self.worker()
        self.assertEqual((await self.settled(task.task_id)).status, "interrupted")
        self.assertIsNone(self.store._ledger.get(task.task_id)["commands"][0]["appliedNodeId"])
        path = self.store._journals[task.task_id].path
        prefix = path.read_bytes()
        self.assertIn(ids[0], prefix.decode())
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        self.reopen()
        await self.worker()
        result = await self.settled(task.task_id)
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(result.attempt_count, 2)
        commands = self.store._ledger.get(task.task_id)["commands"]
        self.assertEqual([c["commandId"] for c in commands], ids)
        self.assertTrue(all(c["appliedNodeId"] for c in commands))
        self.assertTrue(path.read_bytes().startswith(prefix))
        nodes = [json.loads(line) for line in path.read_text().splitlines() if json.loads(line)["type"] == "session.node"]
        for cid in ids:
            self.assertEqual(sum(n["message"].get("commandId") == cid for n in nodes), 1)
        self.assertEqual(len(self.client.writes), 1)

    async def test_stopped_owner_without_final_trace_is_reconstructed_before_next_attempt(self):
        task = await self.store.enqueue(self.alert)
        original = self.store._ledger.record_trace
        interrupted = []

        def crash_before_trace(trace):
            if not interrupted:
                interrupted.append(trace.attempt_id)
                raise asyncio.CancelledError
            original(trace)

        self.store._ledger.record_trace = crash_before_trace
        worker = await self.worker()
        self.assertEqual((await self.settled(task.task_id)).status, "interrupted")
        state = self.store._ledger.get(task.task_id)
        self.assertTrue(state["attempts"][-1]["ownerStopped"])
        self.assertEqual(self.store._ledger.traces(task.task_id), [])
        journal = self.control / "tasks.jsonl"
        before = journal.read_bytes()
        snapshot = (await self.store.get_task_traces(task.task_id))[0]
        self.assertEqual(snapshot["verdict"], "partial-audit")
        self.assertTrue(snapshot["events"][0]["details"]["ownerStopped"])
        self.assertEqual(journal.read_bytes(), before)
        session_path = self.store._journals[task.task_id].path
        prefix = session_path.read_bytes()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        self.reopen()
        await self.worker()
        result = await self.settled(task.task_id)
        self.assertEqual(result.status, "succeeded", result.error)
        final = self.store._ledger.get(task.task_id)
        self.assertEqual(len(final["attempts"]), 2)
        self.assertEqual(final["limits"], state["limits"])
        self.assertEqual(final["timing"], state["timing"])
        traces = self.store._ledger.traces(task.task_id)
        self.assertEqual(len(traces), 2)
        self.assertEqual(traces[0]["attemptId"], interrupted[0])
        self.assertEqual(traces[0]["steps"][0]["tool"], "write_file")
        self.assertEqual(traces[0]["events"][0]["type"], "attempt.recovered")
        self.assertEqual(len(self.client.writes), 1)
        self.assertTrue(session_path.read_bytes().startswith(prefix))
        # 旧attempt缺失的边界不能误指向后续attempt的全局最新节点。
        old_view = self.store._ledger.recovery_trace(task.task_id, interrupted[0])
        self.assertIsNone(old_view.events[0].details["completeNodeId"])

    async def test_unconfirmed_owner_has_read_only_partial_audit_not_completion(self):
        task = await self.store.enqueue(self.alert)
        self.client.unknown_claim = True
        self.store._ledger.record_trace = lambda trace: None  # 显式Fake落盘缺口。
        await self.worker()
        self.assertEqual((await self.settled(task.task_id)).status, "interrupted")
        journal = self.control / "tasks.jsonl"
        before = journal.read_bytes()
        traces = await self.store.get_task_traces(task.task_id)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["mode"], "in-progress-audit")
        details = traces[0]["events"][0]["details"]
        self.assertFalse(details["completionTracePersisted"])
        self.assertFalse(details["ownerStopped"])
        self.assertEqual(await self.store.get_task_traces(task.task_id), traces)
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(self.store._ledger.traces(task.task_id), [])
        self.assertEqual(len(self.store._ledger.get(task.task_id)["attempts"]), 1)

    async def test_v1_old_node_resume_rejected_but_latest_uses_same_workspace(self):
        task = await self.store.enqueue(self.alert)
        worker = await self.worker()
        self.assertEqual((await self.settled(task.task_id)).status, "succeeded")
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        tree = await self.store.get_session_tree(task.session_id)
        old = next(n["nodeId"] for n in tree["nodes"] if n["branchable"] and n["nodeId"] != tree["activeLeafId"])
        with self.assertRaises(TaskConflictError):
            await self.store.resume(task.session_id, old)
        self.assertEqual((await self.store.get_session_tree(task.session_id))["activeLeafId"], tree["activeLeafId"])
        self.assertEqual((await self.store.get_session_path(task.session_id, old))["nodeId"], old)
        resumed = await self.store.resume(task.session_id)
        self.assertNotEqual(resumed.task_id, task.task_id)
        self.assertEqual(resumed.workspace_id, task.workspace_id)
        self.assertEqual(resumed.branch_id, task.branch_id)

    async def test_shutdown_does_not_become_user_cancellation(self):
        task = await self.store.enqueue(self.alert)
        self.client.block_write = True
        worker = await self.worker()
        await asyncio.wait_for(self.client.entered.wait(), 3)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        self.assertFalse(self.store._ledger.get(task.task_id)["cancelRequested"])
        self.client.block_write = False
        self.reopen()
        await self.worker()
        result = await self.settled(task.task_id)
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(len(self.client.writes), 1)

    async def test_cancel_reconciles_blocked_owner_even_with_a_stale_done_handle(self):
        task = await self.store.enqueue(self.alert)
        self.client.unknown_claim = True
        await self.worker()
        self.assertEqual((await self.settled(task.task_id)).status, "interrupted")
        state = self.store._ledger.get(task.task_id)
        attempt_id = state["attempts"][-1]["attemptId"]
        self.client.unknown_claim = False
        # Fake模拟先前认领稍后变为可核查的Go归属；不模拟真正Kubernetes停止。
        await self.client.claim_workspace(task.workspace_id, task.task_id, attempt_id)
        done = asyncio.create_task(asyncio.sleep(0))
        await done
        self.store._running[task.task_id] = done
        await self.store.cancel(task.task_id)
        result = await self.settled(task.task_id)
        self.assertEqual(result.status, "cancelled")
        final = self.store._ledger.get(task.task_id)
        self.assertEqual(len(final["attempts"]), 1)
        self.assertTrue(final["attempts"][0]["ownerStopped"])
        self.assertEqual(len(self.client.writes), 0)
