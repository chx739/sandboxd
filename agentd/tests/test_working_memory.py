from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from agentd.memory_context_eval import run as run_context_comparison
from agentd.app.context import transform_model_context
from agentd.app.model_gateway import ModelInvocation
from agentd.app.models import AlertEvent, ModelUsage
from agentd.app.plugins import build_builtin_registry
from agentd.app.plugins.base import PluginContext
from agentd.app.runtime import AgentControl, AgentLoopState, PiStyleAgentLoop
from agentd.app.runtime.session import SessionJournal
from agentd.app.working_memory import (
    available_evidence_ids, evidence_id, project_working_memory, validate_update,
)


def _call(name: str, call_id: str, args: dict | None = None) -> AIMessage:
    return AIMessage(content="", tool_calls=[{
        "id": call_id, "name": name, "args": args or {}, "type": "tool_call",
    }])


class WorkingMemoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_loop_accepts_same_round_model_proposal_without_summary_call(self) -> None:
        class ScriptedSession:
            def __init__(self, responses: list[AIMessage]) -> None:
                self.responses = responses
                self.calls = 0

            async def invoke(self, messages: Sequence[BaseMessage]) -> ModelInvocation:
                response = self.responses[self.calls]
                self.calls += 1
                return ModelInvocation(
                    message=response, usage=ModelUsage(),
                    finish_reason="stop", elapsed_ms=0,
                )

        ref = evidence_id("task-memory", "missing-memory")
        update = {"hypotheses": [{
            "text": "需核对当前状态", "status": "open", "evidenceIds": [ref],
        }], "pendingChecks": ["重新查询"]}
        session = ScriptedSession([
            _call("read_memory", "missing-memory", {"level": "detail"}),
            _call("update_working_memory", "update-1", update),
            AIMessage(content=json.dumps({
                "summary": "证据不足", "rootCause": "未确认",
                "recommendation": "重新查询",
            }, ensure_ascii=False)),
        ])
        context = PluginContext(
            sandbox_id="sandbox-test",
            prometheus=object(),  # type: ignore[arg-type]
            sandboxd=object(),  # type: ignore[arg-type]
            linux_hosts=object(),  # type: ignore[arg-type]
            workspace=object(),  # type: ignore[arg-type]
        )
        state = await PiStyleAgentLoop(
            session=session, plugins=build_builtin_registry(),
            plugin_context=context, control=AgentControl(),
            state=AgentLoopState(
                task_id="task-memory", alert={"status": "firing"},
                sandbox_id="sandbox-test",
            ),
        ).run()
        note = project_working_memory(state.alert, state.messages)
        self.assertEqual(session.calls, 3)
        self.assertEqual(note["hypotheses"][0]["text"], "需核对当前状态")
        self.assertEqual(len(state.evidence), 1)

    async def test_loop_rejects_unreturned_evidence_id(self) -> None:
        class ScriptedSession:
            def __init__(self) -> None:
                self.calls = 0

            async def invoke(self, messages: Sequence[BaseMessage]) -> ModelInvocation:
                self.calls += 1
                response = (
                    _call("update_working_memory", "bad-update", {"hypotheses": [{
                        "text": "已恢复", "evidenceIds": ["ev-00000000000000000000"],
                    }]}) if self.calls == 1 else
                    AIMessage(content='{"summary":"证据不足","rootCause":"未确认"}')
                )
                return ModelInvocation(response, ModelUsage(), "stop", 0)

        state = await PiStyleAgentLoop(
            session=ScriptedSession(), plugins=build_builtin_registry(),
            plugin_context=PluginContext(
                sandbox_id="fake", prometheus=object(), sandboxd=object(),
                linux_hosts=object(), workspace=object(),
            ),
            control=AgentControl(),
            state=AgentLoopState(task_id="task-invalid", alert={}, sandbox_id="fake"),
        ).run()
        result = next(item for item in state.messages if isinstance(item, ToolMessage))
        self.assertFalse(json.loads(str(result.content))["ok"])
        self.assertEqual(project_working_memory({}, state.messages)["hypotheses"], [])

    async def test_branch_projection_and_incremental_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            alert = AlertEvent(
                labels={"cluster": "demo", "namespace": "payments"},
                annotations={"summary": "payment Pod restarts"},
            )
            journal = SessionJournal(Path(directory), "session-0123456789abcdef")
            await journal.initialize("task-a", alert)
            ref = evidence_id("task-a", "log-1")
            first = [
                SystemMessage(content="rules"), HumanMessage(content="alert"),
                _call("search_logs", "log-1"),
                ToolMessage(content=json.dumps({
                    "ok": True, "evidenceId": ref,
                    "observedAt": "2026-09-29T08:00:00+00:00",
                    "body": {"errorCode": "OOM"},
                }), tool_call_id="log-1"),
            ]
            await journal.append_transcript("task-a", first)
            root = (await journal.tree())["activeLeafId"]
            self.assertIsNotNone(root)
            update = {"hypotheses": [{
                "text": "内存限制不足", "status": "open", "evidenceIds": [ref],
            }], "pendingChecks": ["查询内存限制"]}
            second = first + [
                _call("update_working_memory", "note-1", update),
                ToolMessage(content=json.dumps({
                    "ok": True, "acceptedUpdate": validate_update(update, {ref}),
                }, ensure_ascii=False), tool_call_id="note-1"),
            ]
            await journal.append_transcript("task-a", second)
            await journal.append_transcript("task-a", second)
            self.assertEqual((await journal.summary())["nodeCount"], len(second))
            # 模拟取消发生在下一组工具结果尚未返回时；磁盘可含半轮节点，
            # 但重启恢复只能选之前完整工具轮次的安全叶子。
            await journal.append_transcript(
                "task-a", second + [_call("kubernetes_read", "unfinished")],
            )

            reopened = SessionJournal(Path(directory), journal.session_id)
            _, old_path, _ = await reopened.load_branch(root)
            _, current_path, _ = await reopened.load_branch()
            alert_dict = alert.model_dump(mode="json", by_alias=True)
            self.assertEqual(project_working_memory(alert_dict, old_path)["hypotheses"], [])
            self.assertEqual(
                project_working_memory(alert_dict, current_path)["hypotheses"][0]["text"],
                "内存限制不足",
            )
            self.assertEqual(
                project_working_memory(alert_dict, current_path)["observations"][0]["category"],
                "historical_event",
            )
            await reopened.initialize("task-b", alert, parent_node_id=root)
            sibling = first + [
                _call("update_working_memory", "note-b", {
                    "hypotheses": [{"text": "应用配置不兼容", "status": "open",
                                    "evidenceIds": [ref]}],
                }),
                ToolMessage(content=json.dumps({
                    "ok": True, "acceptedUpdate": {
                        "hypotheses": [{"text": "应用配置不兼容", "status": "open",
                                        "evidenceIds": [ref]}], "pendingChecks": [],
                    },
                }, ensure_ascii=False), tool_call_id="note-b"),
            ]
            await reopened.append_transcript("task-b", sibling)
            _, sibling_path, _ = await reopened.load_branch()
            self.assertEqual(
                [item["text"] for item in project_working_memory(alert_dict, sibling_path)["hypotheses"]],
                ["应用配置不兼容"],
            )
            self.assertEqual(
                [item["text"] for item in project_working_memory(alert_dict, current_path)["hypotheses"]],
                ["内存限制不足"],
            )

    async def test_expired_state_and_invalid_evidence(self) -> None:
        observed = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
        ref = evidence_id("task-b", "pod-1")
        messages = [
            _call("kubernetes_read", "pod-1"),
            ToolMessage(content=json.dumps({
                "ok": True, "evidenceId": ref,
                "observedAt": observed.isoformat(),
                "body": {"metadata": {"uid": "pod-uid-a"}, "phase": "Pending"},
            }), tool_call_id="pod-1"),
        ]
        note = project_working_memory(
            {"labels": {"cluster": "demo"}}, messages,
            now=observed + timedelta(seconds=61),
        )
        self.assertEqual(note["observations"][0]["freshness"], "stale")
        self.assertEqual(note["observations"][0]["resourceUid"], "pod-uid-a")
        self.assertEqual(note["observations"][0]["identity"], "unverified")
        view = transform_model_context(
            [SystemMessage(content="rules"), HumanMessage(content="alert"), *messages],
            working_memory=note,
        )
        self.assertTrue(json.loads(str(view.messages[-1].content))[
            "requiresRecheckForCurrentClaim"
        ])
        with self.assertRaisesRegex(ValueError, "不存在的证据"):
            validate_update({"hypotheses": [{
                "text": "已恢复", "evidenceIds": ["ev-00000000000000000000"],
            }]}, {ref})

    async def test_old_branch_evidence_can_still_be_referenced(self) -> None:
        messages: list[BaseMessage] = []
        for index in range(20):
            call_id = f"evidence-{index}"
            messages.extend([
                _call("search_logs", call_id),
                ToolMessage(content=json.dumps({
                    "ok": True, "evidenceId": evidence_id("task-many", call_id),
                    "observedAt": "2026-09-29T08:00:00+00:00",
                }), tool_call_id=call_id),
            ])
        self.assertLess(len(project_working_memory({}, messages)["observations"]), 20)
        first = evidence_id("task-many", "evidence-0")
        self.assertNotIn(first, {item["evidenceId"] for item in
                                 project_working_memory({}, messages)["observations"]})
        self.assertIn(first, available_evidence_ids(messages))
        self.assertEqual(validate_update({"hypotheses": [{
            "text": "需检查早期错误", "evidenceIds": [first],
        }]}, available_evidence_ids(messages))["hypotheses"][0]["evidenceIds"], [first])
        messages.append(AIMessage(content=json.dumps({"workingMemoryUpdate": {
            "hypotheses": [{"text": "保留早期线索", "evidenceIds": [first]}],
        }}, ensure_ascii=False)))
        cited = project_working_memory({}, messages)["citedEvidence"]
        self.assertEqual(cited[0]["evidenceId"], first)
        self.assertEqual(cited[0]["tool"], "search_logs")
        self.assertTrue(cited[0]["observedAt"])

    async def test_fenced_final_answer_can_propose_pending_check(self) -> None:
        messages = [AIMessage(content=(
            '诊断如下：\n```json\n{"summary":"待查",'
            '"workingMemoryUpdate":{"hypotheses":[{"text":"需核对 UID",'
            '"evidenceIds":[]}],"pendingChecks":["读取 Pod UID"]}}\n```'
        ))]
        note = project_working_memory({}, messages)
        self.assertEqual(note["hypotheses"][0]["provenance"], "model-proposed")
        self.assertEqual(note["pendingChecks"], ["读取 Pod UID"])

    async def test_current_state_requires_matching_resource_identity(self) -> None:
        instant = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
        ref = evidence_id("task-identity", "deployment")
        messages = [
            _call("kubernetes_read", "deployment", {
                "operation": "get_deployment", "namespace": "payments", "name": "api",
            }),
            ToolMessage(content=json.dumps({
                "ok": True, "evidenceId": ref, "observedAt": instant.isoformat(),
                "body": {"cluster": "demo", "metadata": {
                    "uid": "uid-api", "namespace": "payments",
                }},
            }), tool_call_id="deployment"),
        ]
        matched = project_working_memory({"labels": {
            "cluster": "demo", "namespace": "payments", "workload": "api",
        }}, messages, now=instant + timedelta(seconds=30))
        self.assertEqual(matched["observations"][0]["identity"], "matched")
        wrong = project_working_memory({"labels": {
            "cluster": "demo", "namespace": "payments", "workload": "checkout",
        }}, messages, now=instant + timedelta(seconds=30))
        self.assertEqual(wrong["observations"][0]["identity"], "mismatch")
        no_namespace = project_working_memory({"labels": {
            "cluster": "demo", "workload": "api",
        }}, messages, now=instant + timedelta(seconds=30))
        self.assertEqual(no_namespace["observations"][0]["identity"], "unverified")

    async def test_context_keeps_note_and_tool_protocol_with_budget(self) -> None:
        ref = evidence_id("task-c", "last")
        note = {"goal": "排查", "observations": [{"evidenceId": ref}],
                "hypotheses": [], "completedChecks": [], "pendingChecks": ["核对状态"]}
        messages = [
            SystemMessage(content="rules"), HumanMessage(content="alert"),
            _call("search_logs", "old"),
            ToolMessage(content="x" * 900, tool_call_id="old"),
            _call("kubernetes_read", "last"),
            ToolMessage(content=json.dumps({"ok": True, "evidenceId": ref}),
                        tool_call_id="last"),
        ]
        result = transform_model_context(
            messages, budget=750, working_memory=note,
            tool_schemas=[{"type": "function", "function": {"name": "check"}}],
            token_budget=2000, output_reserve=200,
        )
        self.assertTrue(result.trimmed)
        self.assertLessEqual(result.after_chars, 750)
        self.assertLessEqual(result.estimated_tokens, 2000)
        self.assertIn("task-working-memory", str(result.messages[2].content))
        self.assertEqual(
            [item.tool_call_id for item in result.messages if isinstance(item, ToolMessage)],
            ["last"],
        )

    async def test_long_dialogue_keeps_early_pointer_and_latest_denial(self) -> None:
        result = run_context_comparison()
        self.assertFalse(result["old"]["firstEvidenceVisible"])
        self.assertFalse(result["old"]["latestDenialVisible"])
        self.assertTrue(result["new"]["firstEvidencePointerVisible"])
        self.assertTrue(result["new"]["latestDenialVisible"])
        self.assertLessEqual(result["new"]["estimatedTokensWithOutputReserve"], 32768)

    async def test_old_denial_cannot_evict_latest_user_input(self) -> None:
        messages: list[BaseMessage] = [
            SystemMessage(content="rules"), HumanMessage(content="alert"),
            _call("write_file", "denied-old"),
            ToolMessage(content=json.dumps({
                "ok": False, "denied": True, "denyLayer": "agent-policy",
                "evidenceId": evidence_id("task-budget", "denied-old"),
                "error": "blocked" * 100,
            }), tool_call_id="denied-old"),
            AIMessage(content="previous turn completed"),
            HumanMessage(content="最新追问" + "x" * 480),
        ]
        with self.assertRaisesRegex(ValueError, "最新交互"):
            transform_model_context(messages, budget=600,
                                    token_budget=2000, output_reserve=100)


if __name__ == "__main__":
    unittest.main()
