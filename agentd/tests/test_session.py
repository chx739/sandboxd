from __future__ import annotations

import json
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agentd.app.models import AlertEvent
from agentd.app.runtime.session import SessionJournal
from agentd.app.store import TaskConflictError, TaskStore


class SessionJournalTest(unittest.IsolatedAsyncioTestCase):
    async def test_jsonl_is_private_redacted_and_resumable(self) -> None:
        # WSL 会把 Windows TEMP 注入到 /mnt/c；DrvFS 未启用 metadata 时无法
        # 验证 POSIX 权限，因此安全权限测试必须放在原生 Linux 文件系统。
        with TemporaryDirectory(dir="/tmp") as directory:
            session_dir = Path(directory) / "sessions"
            journal = SessionJournal(session_dir, "session-0123456789abcdef")
            alert = AlertEvent(
                labels={"alertname": "HighCPU"},
                annotations={
                    "summary": "CPU high",
                    "description": "Authorization: Bearer alert-secret-value",
                },
            )
            await journal.initialize("task-first", alert)
            await journal.append_command(
                "task-first",
                "steer",
                "Authorization: Bearer session-super-secret",
            )
            messages = [
                SystemMessage(content="system"),
                HumanMessage(content="diagnose"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call-1",
                            "name": "query_prometheus",
                            "args": {"query": "api_key=sk-1234567890"},
                        }
                    ],
                ),
                ToolMessage(content='{"ok":true}', tool_call_id="call-1"),
                AIMessage(content='{"summary":"done"}'),
            ]
            await journal.append_transcript("task-first", messages)
            await journal.append_result("task-first", "succeeded", "done")

            raw = journal.path.read_text(encoding="utf-8")
            self.assertNotIn("session-super-secret", raw)
            self.assertNotIn("alert-secret-value", raw)
            self.assertNotIn("sk-1234567890", raw)
            # 每一行都必须是完整 JSON；进程中断时最多丢失最后一行。
            for line in raw.splitlines():
                self.assertIsInstance(json.loads(line), dict)

            self.assertEqual(stat.S_IMODE(session_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(journal.path.stat().st_mode), 0o600)
            summary = await journal.summary()
            self.assertEqual(summary["messageCount"], len(messages))
            self.assertEqual(summary["commandCount"], 1)
            resumed_alert, resumed_messages = await journal.load_for_resume()
            self.assertEqual(resumed_alert.labels["alertname"], "HighCPU")
            self.assertEqual(len(resumed_messages), len(messages))


class TaskStoreSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_queued_control_cancel_and_resume_identity(self) -> None:
        with TemporaryDirectory(dir="/tmp") as directory:
            store = TaskStore(Path(directory))
            original = await store.enqueue(
                AlertEvent(annotations={"summary": "CPU high"})
            )
            await store.send_control(original.task_id, "steer", "先查 CPU")
            cancelled = await store.cancel(original.task_id)
            self.assertEqual(cancelled.status, "cancelled")
            with self.assertRaises(TaskConflictError):
                await store.send_control(
                    original.task_id,
                    "follow-up",
                    "再补充建议",
                )

            journal = SessionJournal(
                Path(directory) / "sessions",
                original.session_id,
            )
            await journal.append_transcript(
                original.task_id,
                [
                    SystemMessage(content="system"),
                    HumanMessage(content="diagnose"),
                    AIMessage(content='{"summary":"first"}'),
                ],
            )
            resumed = await store.resume(original.session_id)
            self.assertNotEqual(resumed.task_id, original.task_id)
            self.assertEqual(resumed.session_id, original.session_id)
            summary = await store.get_session(original.session_id)
            self.assertEqual(summary["runCount"], 2)
            self.assertEqual(summary["taskId"], resumed.task_id)
            self.assertEqual(summary["status"], "running")


if __name__ == "__main__":
    unittest.main()
