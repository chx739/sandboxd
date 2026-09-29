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

    async def test_branch_keeps_old_path_and_survives_restart(self) -> None:
        with TemporaryDirectory(dir="/tmp") as directory:
            session_dir = Path(directory) / "sessions"
            session_id = "session-0123456789abcdef"
            journal = SessionJournal(session_dir, session_id)
            alert = AlertEvent(annotations={"summary": "CPU high"})
            await journal.initialize("task-first", alert)
            first = [
                SystemMessage(content="safety"),
                HumanMessage(content="diagnose"),
                AIMessage(content="first answer"),
                HumanMessage(content="continue"),
                AIMessage(content="old ending"),
            ]
            await journal.append_transcript("task-first", first)
            original = await journal.tree()
            fork_id = original["nodes"][2]["nodeId"]
            old_leaf = original["activeLeafId"]
            self.assertTrue(original["nodes"][2]["branchable"])

            _, prefix, selected = await journal.load_branch(fork_id)
            self.assertEqual(selected, fork_id)
            await journal.initialize("task-fork", alert, selected)
            await journal.append_transcript(
                "task-fork",
                prefix + [HumanMessage(content="alternate"), AIMessage(content="new ending")],
            )
            reopened = SessionJournal(session_dir, session_id)
            tree = await reopened.tree()
            self.assertEqual(len(tree["nodes"]), 7)
            old_path = await reopened.path_messages(old_leaf)
            new_path = await reopened.path_messages(tree["activeLeafId"])
            self.assertEqual(old_path["messages"][-1]["content"], "old ending")
            self.assertEqual(new_path["messages"][-1]["content"], "new ending")
            self.assertNotIn("old ending", [item["content"] for item in new_path["messages"]])
            _, resumed = await reopened.load_for_resume()
            self.assertEqual(resumed[-1].content, "new ending")

    async def test_incomplete_tool_call_is_not_branchable(self) -> None:
        with TemporaryDirectory(dir="/tmp") as directory:
            journal = SessionJournal(
                Path(directory), "session-0123456789abcdef"
            )
            await journal.initialize("task-first", AlertEvent())
            await journal.append_transcript(
                "task-first",
                [
                    SystemMessage(content="safety"),
                    HumanMessage(content="diagnose"),
                    AIMessage(content="", tool_calls=[{
                        "id": "call-1", "name": "query_prometheus", "args": {}
                    }]),
                ],
            )
            tree = await journal.tree()
            self.assertEqual(tree["activeLeafId"], tree["nodes"][1]["nodeId"])
            self.assertFalse(tree["nodes"][-1]["branchable"])
            with self.assertRaises(ValueError):
                await journal.load_branch(tree["nodes"][-1]["nodeId"])

    async def test_tool_group_can_branch_only_after_result(self) -> None:
        with TemporaryDirectory(dir="/tmp") as directory:
            journal = SessionJournal(
                Path(directory), "session-0123456789abcdef"
            )
            alert = AlertEvent()
            await journal.initialize("task-first", alert)
            first = [
                SystemMessage(content="safety"),
                HumanMessage(content="diagnose"),
                AIMessage(content="", tool_calls=[{
                    "id": "call-1", "name": "query_prometheus", "args": {"query": "up"}
                }]),
                ToolMessage(content="metric result", tool_call_id="call-1"),
                AIMessage(content="first answer"),
            ]
            await journal.append_transcript("task-first", first)
            tree = await journal.tree()
            self.assertFalse(tree["nodes"][2]["branchable"])
            self.assertTrue(tree["nodes"][3]["branchable"])
            tool_result_id = tree["nodes"][3]["nodeId"]
            _, prefix, selected = await journal.load_branch(tool_result_id)
            await journal.initialize("task-fork", alert, selected)
            await journal.append_transcript(
                "task-fork",
                prefix + [HumanMessage(content="recheck"), AIMessage(content="second answer")],
            )
            _, resumed = await journal.load_for_resume()
            self.assertEqual(resumed[-1].content, "second answer")

    async def test_truncated_tail_is_ignored_and_repaired(self) -> None:
        with TemporaryDirectory(dir="/tmp") as directory:
            journal = SessionJournal(
                Path(directory), "session-0123456789abcdef"
            )
            await journal.initialize("task-first", AlertEvent())
            await journal.append_transcript(
                "task-first",
                [SystemMessage(content="safety"), AIMessage(content="done")],
            )
            with journal.path.open("ab") as handle:
                handle.write(b'{"type":"torn"')
            self.assertIsNotNone((await journal.tree())["activeLeafId"])
            await journal.append_command("task-first", "steer", "continue")
            for line in journal.path.read_text(encoding="utf-8").splitlines():
                json.loads(line)

    async def test_old_linear_snapshot_is_migrated(self) -> None:
        with TemporaryDirectory(dir="/tmp") as directory:
            journal = SessionJournal(
                Path(directory), "session-0123456789abcdef"
            )
            alert = AlertEvent(annotations={"summary": "old"})
            journal.path.write_text(
                "\n".join([
                    json.dumps({
                        "type": "session.header",
                        "taskId": "old-task",
                        "alert": alert.model_dump(mode="json", by_alias=True),
                    }),
                    json.dumps({
                        "type": "session.transcript",
                        "taskId": "old-task",
                        "messages": [
                            {"role": "system", "content": "safety"},
                            {"role": "user", "content": "diagnose"},
                            {"role": "assistant", "content": "done", "toolCalls": []},
                        ],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            tree = await journal.tree()
            self.assertEqual(len(tree["nodes"]), 3)
            _, resumed = await journal.load_for_resume()
            self.assertEqual(resumed[-1].content, "done")


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

    async def test_resume_from_selected_node_creates_new_task(self) -> None:
        with TemporaryDirectory(dir="/tmp") as directory:
            store = TaskStore(Path(directory))
            first = await store.enqueue(AlertEvent(annotations={"summary": "CPU high"}))
            journal = SessionJournal(Path(directory) / "sessions", first.session_id)
            await journal.append_transcript(
                first.task_id,
                [
                    SystemMessage(content="safety"),
                    HumanMessage(content="diagnose"),
                    AIMessage(content="first"),
                    HumanMessage(content="continue"),
                    AIMessage(content="old ending"),
                ],
            )
            tree = await store.get_session_tree(first.session_id)
            fork_id = tree["nodes"][2]["nodeId"]
            branched = await store.resume(first.session_id, fork_id)
            self.assertNotEqual(branched.task_id, first.task_id)
            self.assertEqual(branched.session_id, first.session_id)
            self.assertEqual((await store.get_session_tree(first.session_id))["activeLeafId"], fork_id)
            context = store._resume_messages[branched.task_id]
            self.assertEqual(context[-2].content, "first")
            self.assertNotIn("old ending", [message.content for message in context])


if __name__ == "__main__":
    unittest.main()
