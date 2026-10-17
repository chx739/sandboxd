from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agentd.app.models import AlertEvent
from agentd.app.runtime.session import SessionJournal


class SessionAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="sandboxd-session-attempt-", dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.journal = SessionJournal(self.root, "session-0123456789abcdef")
        self.first_id = "attempt-" + "1" * 32
        self.second_id = "attempt-" + "2" * 32
        self.first = self.journal.for_attempt(self.first_id)
        self.alert = AlertEvent()
        self.prefix = [SystemMessage(content="rules"), HumanMessage(content="alert")]
        await self.first.initialize("task-same", self.alert)

    def entries(self) -> list[dict]:
        return [json.loads(line) for line in self.journal.path.read_text().splitlines()]

    async def test_same_task_new_attempt_keeps_complete_prefix_without_duplicates(self) -> None:
        complete = [*self.prefix, AIMessage(content="checked A")]
        head = await self.first.append_transcript("task-same", complete)
        original = self.journal.path.read_bytes()
        reopened = SessionJournal(self.root, self.journal.session_id)
        second = reopened.for_attempt(self.second_id)
        _, messages, selected = await reopened.load_branch()
        self.assertEqual(selected, head)
        await second.initialize("task-same", self.alert, selected)
        new_head = await second.append_transcript("task-same", [*messages, AIMessage(content="checked B")])
        # 相同边界重复持久化幂等；原 JSONL 字节前缀不改变。
        before = self.journal.path.read_bytes()
        self.assertEqual(await second.append_transcript("task-same", [*messages, AIMessage(content="checked B")]), new_head)
        self.assertEqual(self.journal.path.read_bytes(), before)
        self.assertTrue(before.startswith(original))
        _, resumed, _ = await reopened.load_branch()
        self.assertEqual([message.content for message in resumed], ["rules", "alert", "checked A", "checked B"])
        nodes = [entry for entry in self.entries() if entry["type"] == "session.node"]
        self.assertEqual(sum(entry["attemptId"] == self.first_id for entry in nodes), 3)
        self.assertEqual(sum(entry["attemptId"] == self.second_id for entry in nodes), 1)

    async def test_incomplete_tool_group_is_audited_but_not_loaded_by_new_attempt(self) -> None:
        safe = await self.first.append_transcript("task-same", self.prefix)
        interrupted = [*self.prefix, AIMessage(content="", tool_calls=[
            {"id": "call-write", "name": "write_file", "args": {"path": "report.md", "content": "draft"}},
        ])]
        self.assertEqual(await self.first.append_transcript("task-same", interrupted), safe)
        _, recovered, selected = await self.journal.load_branch()
        self.assertEqual(len(recovered), 2)
        second = self.journal.for_attempt(self.second_id)
        await second.initialize("task-same", self.alert, selected)
        final = [*recovered, AIMessage(content="write outcome needs inspection")]
        await second.append_transcript("task-same", final)
        _, messages, _ = await self.journal.load_branch()
        self.assertEqual(len(messages), 3)
        self.assertFalse(any(isinstance(m, ToolMessage) or getattr(m, "tool_calls", []) for m in messages))
        self.assertTrue(any(entry.get("attemptId") == self.first_id and entry.get("message", {}).get("toolCalls")
                            for entry in self.entries()))

    async def test_stale_attempt_cannot_append_or_reactivate_after_switch(self) -> None:
        head = await self.first.append_transcript("task-same", self.prefix)
        second = self.journal.for_attempt(self.second_id)
        await second.initialize("task-same", self.alert, head)
        before = self.journal.path.read_bytes()
        with self.assertRaises(ValueError):
            await self.first.append_transcript("task-same", [*self.prefix, AIMessage(content="stale")])
        with self.assertRaises(ValueError):
            await self.first.append_result("task-same", "failed", "stale result")
        with self.assertRaises(ValueError):
            await self.first.initialize("task-same", self.alert)
        self.assertEqual(self.journal.path.read_bytes(), before)

    async def test_attempt_start_is_idempotent_but_rebinding_identity_is_rejected(self) -> None:
        original = self.journal.path.read_bytes()
        await self.first.initialize("task-same", self.alert)
        self.assertEqual(original, self.journal.path.read_bytes())
        with self.assertRaises(ValueError):
            await self.first.initialize("task-other", self.alert)
        with self.assertRaises(ValueError):
            await self.first.append_transcript("task-other", self.prefix)

    async def test_attempt_cannot_select_old_complete_boundary_with_latest_files(self) -> None:
        old = await self.first.append_transcript("task-same", self.prefix)
        await self.first.append_transcript("task-same", [*self.prefix, AIMessage(content="newer")])
        second = self.journal.for_attempt(self.second_id)
        with self.assertRaises(ValueError):
            await second.initialize("task-same", self.alert, old)
        # 旧节点仍可读取；这是 Session 尝试接口检查，非完整 V1 API 验收。
        self.assertEqual((await self.journal.path_messages(old))["nodeId"], old)

    async def test_legacy_records_remain_readable_without_rewriting(self) -> None:
        legacy = SessionJournal(self.root, "session-fedcba9876543210")
        await legacy.initialize("task-legacy", self.alert)
        head = await legacy.append_transcript("task-legacy", self.prefix)
        original = legacy.path.read_bytes()
        view = legacy.for_attempt(self.first_id)
        await view.initialize("task-legacy", self.alert, head)
        _, messages, _ = await view.load_branch()
        await view.append_transcript("task-legacy", [*messages, AIMessage(content="continued")])
        self.assertTrue(legacy.path.read_bytes().startswith(original))
        self.assertNotIn(b"attemptId", original)
        tree = await legacy.tree()
        self.assertEqual([node["attemptId"] for node in tree["nodes"]], [None, None, self.first_id])

    async def test_attempt_views_share_lock_and_reject_invalid_ids(self) -> None:
        second = self.journal.for_attempt(self.second_id)
        self.assertIs(self.first._lock, second._lock)
        for invalid in ("../attempt", "attempt-short", "attempt-" + "z" * 32, None):
            with self.assertRaises(ValueError):
                self.journal.for_attempt(invalid)

    async def test_attempt_view_reads_old_linear_snapshot_as_legacy(self) -> None:
        legacy = SessionJournal(self.root, "session-abcdef0123456789")
        raw = [
            {"type": "session.header", "taskId": "task-legacy", "alert": self.alert.model_dump(mode="json", by_alias=True)},
            {"type": "session.transcript", "taskId": "task-legacy", "messages": [
                {"role": "system", "content": "rules"}, {"role": "user", "content": "alert"},
                {"role": "assistant", "content": "done", "toolCalls": []},
            ]},
        ]
        original = "".join(json.dumps(entry) + "\n" for entry in raw).encode()
        legacy.path.write_bytes(original)
        view = legacy.for_attempt(self.first_id)
        _, messages, head = await view.load_branch()
        self.assertEqual(len(messages), 3)
        tree = await legacy.tree()
        self.assertTrue(all(node["attemptId"] is None for node in tree["nodes"]))
        await view.initialize("task-legacy", self.alert, head)
        await view.append_transcript("task-legacy", [*messages, AIMessage(content="continued")])
        self.assertTrue(legacy.path.read_bytes().startswith(original))
