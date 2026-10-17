from __future__ import annotations

import json
import unittest

import httpx
from langchain_core.messages import AIMessage

from agentd.app.clients import SandboxdClient
from agentd.app.plugins.base import PluginContext
from agentd.app.plugins.files import FileToolsPlugin
from agentd.app.tools.remote_files import RemoteFileWorkspace, WorkspaceBinding, WorkspaceExecutionError
from agentd.app.plugins.registry import build_builtin_registry
from agentd.app.runtime import AgentControl, AgentLoopState, PiStyleAgentLoop
from agentd.tests.test_runtime_loop import SequenceSession


class RemoteWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.binding = WorkspaceBinding("workspace-" + "a" * 32, "task-a", "attempt-a", "sandbox-a", 1)

    async def client(self, handler) -> SandboxdClient:
        client = SandboxdClient("http://local-sandboxd.invalid", "synthetic-agent-token")
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.close)
        return client

    async def test_file_plugin_uses_remote_binding_without_local_workspace(self) -> None:
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            self.assertEqual(request.url.path, "/api/v1/workspaces/" + self.binding.workspace_id + "/files")
            return httpx.Response(200, json={"content": "from Pod", "sha256": "a" * 64})
        client = await self.client(handler)
        remote = RemoteFileWorkspace(client, self.binding)
        context = PluginContext("sandbox-a", None, client, None, remote)
        result = await FileToolsPlugin().execute("read_file", {"path": "report.md"}, context)
        self.assertEqual(result.body["content"], "from Pod")
        self.assertEqual(seen[0]["binding"], self.binding.as_request())
        self.assertEqual(seen[0]["request"], {"operation": "read_file", "path": "report.md"})
        self.assertFalse(hasattr(remote, "root"))

    async def test_go_policy_rejection_is_preserved(self) -> None:
        client = await self.client(lambda request: httpx.Response(403, json={"denyLayer": "workspace-policy", "error": "CAS mismatch"}))
        result = await RemoteFileWorkspace(client, self.binding).execute("write_file", {"path": "report.md", "content": "new"})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.body["denyLayer"], "workspace-policy")

    async def test_transport_loss_does_not_retry_or_claim_write_did_not_happen(self) -> None:
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("synthetic timeout")
        client = await self.client(handler)
        with self.assertRaises(WorkspaceExecutionError) as caught:
            await RemoteFileWorkspace(client, self.binding).execute("write_file", {"path": "report.md", "content": "new"})
        self.assertTrue(caught.exception.recoverable)
        self.assertTrue(caught.exception.outcome_uncertain)
        self.assertEqual(len(calls), 1)

    async def test_runtime_stops_attempt_instead_of_asking_model_to_repeat_unknown_write(self) -> None:
        def handler(request):
            raise httpx.ReadTimeout("synthetic timeout")
        client = await self.client(handler)
        remote = RemoteFileWorkspace(client, self.binding)
        session = SequenceSession([AIMessage(content="", tool_calls=[{
            "name": "write_file", "args": {"path": "report.md", "content": "draft"},
            "id": "write-1", "type": "tool_call",
        }])])
        state = AgentLoopState("task-a", {}, "sandbox-a")
        loop = PiStyleAgentLoop(session, build_builtin_registry(),
                                PluginContext("sandbox-a", None, client, None, remote),
                                AgentControl(), state)
        with self.assertRaises(WorkspaceExecutionError):
            await loop.run()
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(state.tool_call_count, 1)
        self.assertTrue(any(event.type == "tool.interrupted" for event in state.events))
        self.assertFalse(any(event.type == "turn.completed" for event in state.events))

    async def test_backend_and_stale_owner_failures_are_distinct(self) -> None:
        for status, body, expected in [
            (503, {"sandboxRecovery": True}, True),
            (503, {"sandboxRecovery": False}, False),
            (409, {"denyLayer": "workspace-ownership"}, False),
        ]:
            client = await self.client(lambda request: httpx.Response(status, json=body))
            with self.assertRaises(WorkspaceExecutionError) as caught:
                await RemoteFileWorkspace(client, self.binding).execute("read_file", {"path": "report.md"})
            self.assertEqual(caught.exception.recoverable, expected)
            self.assertFalse(caught.exception.outcome_uncertain)

    async def test_file_response_has_its_own_bounded_limit(self) -> None:
        content = "x" * (80 << 10)
        client = await self.client(lambda request: httpx.Response(200, json={"content": content}))
        result = await RemoteFileWorkspace(client, self.binding).execute("read_file", {"path": "report.md"})
        self.assertEqual(len(result.body["content"]), len(content))

    async def test_operation_cannot_be_replaced_by_model_arguments(self) -> None:
        client = await self.client(lambda request: self.fail("request should not be sent"))
        with self.assertRaises(ValueError):
            await RemoteFileWorkspace(client, self.binding).execute("read_file", {"operation": "shell", "path": "a"})

    def test_binding_cannot_choose_free_endpoint_or_zero_generation(self) -> None:
        for workspace, generation in [("../plans", 1), ("workspace-" + "a" * 32, 0), ("workspace-" + "a" * 32, True)]:
            with self.assertRaises(ValueError):
                WorkspaceBinding(workspace, "task-a", "attempt-a", "sandbox-a", generation)
