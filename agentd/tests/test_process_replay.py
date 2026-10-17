from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agentd.app.model_gateway import ReplayModelSession
from agentd.app.runtime.durable import DurableJournal
from agentd.workspace_process_demo import entries, state_for


class ProcessReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_delay_is_bounded_and_does_not_enable_live_calls(self):
        session = ReplayModelSession([{"delayMs": 20, "content": "fixture"}])
        invocation = await session.invoke([])
        self.assertEqual(invocation.message.content, "fixture")
        self.assertGreaterEqual(invocation.elapsed_ms, 10)
        for value in (-1, 60001, True, 0.5, "20"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await ReplayModelSession([{"delayMs": value}]).invoke([])

    async def test_cancelled_inflight_replay_slot_is_not_returned_again(self):
        session = ReplayModelSession([{"delayMs": 60000, "content": "lost"}, {"content": "next"}])
        task = asyncio.create_task(session.invoke([]))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual((await session.invoke([])).message.content, "next")
        restored = ReplayModelSession([{"content": "old"}, {"content": "restored"}], cursor=1)
        self.assertEqual((await restored.invoke([])).message.content, "restored")

    async def test_read_only_monitor_checks_python_checksum_and_ignores_torn_tail(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "control/tasks.jsonl"
            with DurableJournal(path) as journal:
                journal.append("task.state", {"taskId": "task-fixture", "status": "running"})
                # 同进程持锁期间只读，不试图打开第二个写入者。
                self.assertEqual(state_for(path, "task-fixture")["status"], "running")
            with path.open("ab") as handle:
                handle.write(b'{"torn":')
            before = path.read_bytes()
            self.assertEqual(len(entries(path)), 1)
            self.assertEqual(path.read_bytes(), before)
            path.write_bytes(before.replace(b'"running"', b'"changed"'))
            with self.assertRaises(AssertionError):
                entries(path)

    async def test_go_reader_uses_original_struct_order_and_raw_data(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "go.jsonl"
            payload = b'{"seq":1,"type":"workspace.state","timestamp":"2026-10-17T00:00:00Z","data":{"note":"\\u003cfixture\\u003e"}}'
            digest = hashlib.sha256(payload).hexdigest()
            path.write_bytes(payload[:-1] + b',"sha256":' + json.dumps(digest).encode() + b'}\n')
            self.assertEqual(entries(path, go_format=True)[0]["data"]["note"], "<fixture>")
            with self.assertRaises(AssertionError):
                entries(path)
