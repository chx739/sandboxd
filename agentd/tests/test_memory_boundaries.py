from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agentd.app.clients import HTTPResult
from agentd.app.model_gateway import ModelInvocation
from agentd.app.models import AlertEvent, ModelUsage
from agentd.app.plugins import build_builtin_registry
from agentd.app.plugins.base import PluginContext
from agentd.app.runtime import AgentControl, AgentLoopState, PiStyleAgentLoop
from agentd.app.runtime.loop import SYSTEM_PROMPT
from agentd.app.runtime.session import SessionJournal
from agentd.app.working_memory import (
    evidence_id, project_working_memory, state_freshness, validate_update,
)


def call(name, identifier, args=None):
    return AIMessage(content="", tool_calls=[{
        "id": identifier, "name": name, "args": args or {}, "type": "tool_call",
    }])


class ScriptedSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.inputs = []

    async def invoke(self, messages):
        self.inputs.append(list(messages))
        return ModelInvocation(next(self.responses), ModelUsage(), "stop", 0)


def loop(session, messages=(), sandbox=None):
    return PiStyleAgentLoop(
        session, build_builtin_registry(),
        PluginContext("fake", None, sandbox, None, None), AgentControl(),
        AgentLoopState("task-boundary", {}, "fake", messages=list(messages)),
    )


class MemoryBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_omitted_pending_checks_are_preserved_and_oversized_update_rejected(self):
        messages = []
        for index, update in enumerate([
            {"pendingChecks": ["check UID"]},
            {"hypotheses": [{"text": "possible OOM", "evidenceIds": []}]},
        ]):
            messages.extend([
                call("update_working_memory", str(index), update),
                ToolMessage(content=json.dumps({
                    "ok": True, "acceptedUpdate": validate_update(update, set()),
                }), tool_call_id=str(index)),
            ])
        self.assertEqual(project_working_memory({}, messages)["pendingChecks"], ["check UID"])
        with self.assertRaisesRegex(ValueError, "预算"):
            validate_update({"pendingChecks": [str(i) + "中" * 199 for i in range(12)]}, set())

    async def test_prometheus_old_sample_cannot_become_current_fact(self):
        now = datetime.now(timezone.utc)
        payload = {
            "ok": True, "observedAt": now.isoformat(),
            "evidenceId": evidence_id("task-old", "metric"),
            "body": {"data": {"resultType": "vector", "result": [{
                "metric": {"pod": "api"},
                "value": [(now - timedelta(minutes=4)).timestamp(), "1"],
            }]}},
        }
        self.assertEqual(state_freshness("query_prometheus", payload, now), "stale")
        history = [SystemMessage(content="old rules"), HumanMessage(content="alert"),
                   call("query_prometheus", "metric", {"query": "up"}),
                   ToolMessage(content=json.dumps(payload), tool_call_id="metric")]
        session = ScriptedSession([AIMessage(content=json.dumps({
            "summary": "当前 Pod 已恢复", "rootCause": "当前没有问题",
        }))])
        state = await loop(session, history).run()
        self.assertIn("当前状态未核实", state.diagnosis.summary)
        self.assertNotIn("当前 Pod 已恢复", state.diagnosis.summary)
        self.assertEqual(len(session.inputs), 1)
        self.assertEqual(session.inputs[0][0].content, SYSTEM_PROMPT)
        self.assertTrue(state.diagnosis.evidence[0].source.startswith("session-history:"))
        self.assertEqual(json.loads(state.messages[-1].content)["summary"], "当前 Pod 已恢复")

    async def test_real_stdout_uid_survives_runtime_truncation(self):
        class Sandbox:
            async def kubernetes_read(self, sandbox_id, arguments):
                return HTTPResult(200, {
                    "operation": "get_deployment", "exitCode": 0,
                    "outputTruncated": False,
                    "stdout": json.dumps({"metadata": {"uid": "real-uid"},
                                          "padding": "x" * 8000}),
                })
        session = ScriptedSession([
            call("kubernetes_read", "deployment", {
                "operation": "get_deployment", "namespace": "sandboxd-target", "name": "api",
            }), AIMessage(content='{"summary":"await verification"}'),
        ])
        state = await loop(session, sandbox=Sandbox()).run()
        result = next(message for message in state.messages if isinstance(message, ToolMessage))
        payload = json.loads(result.content)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["resourceUid"], "real-uid")
        note = project_working_memory({}, state.messages)
        self.assertEqual(note["observations"][0]["resourceUid"], "real-uid")
        self.assertEqual(note["observations"][0]["identity"], "unverified")

    async def test_first_incomplete_turn_recovers_initial_alert(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            journal = SessionJournal(Path(directory), "session-abcdef0123456789")
            await journal.initialize("task-first", AlertEvent())
            await journal.append_transcript("task-first", [
                SystemMessage(content="rules"), HumanMessage(content="alert"),
                call("kubernetes_read", "unfinished"),
            ])
            _, messages, _ = await journal.load_branch()
            self.assertEqual(len(messages), 2)
            self.assertIsInstance(messages[-1], HumanMessage)

    async def test_resumed_branch_preserves_denial(self):
        history = [SystemMessage(content="old"), HumanMessage(content="alert"),
                   call("exec", "denied"), ToolMessage(content=json.dumps({
                       "ok": False, "denied": True, "denyLayer": "agent-policy",
                       "error": "blocked", "evidenceId": evidence_id("task-old", "denied"),
                   }), tool_call_id="denied")]
        state = await loop(ScriptedSession([AIMessage(content='{"summary":"done"}')]), history).run()
        self.assertEqual(len(state.diagnosis.denied_actions), 1)
        self.assertTrue(state.diagnosis.denied_actions[0].action.startswith("historical:"))


if __name__ == "__main__":
    unittest.main()
