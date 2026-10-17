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


class DurableTaskStoreTests(unittest.IsolatedAsyncioTestCase):
    """真实TaskStore/Runner + Fake Go服务；不冒称真实NFS或SIGKILL验收。"""
    async def asyncSetUp(self):
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
