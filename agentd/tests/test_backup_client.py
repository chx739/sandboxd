from __future__ import annotations

import json
import unittest

import httpx

from agentd.app.clients import SandboxdClient


class BackupClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.binding = {"workspaceId": "workspace-" + "1" * 32, "taskId": "task-a",
                        "attemptId": "attempt-a", "sandboxId": "sandbox-a", "generation": 1}
        self.cp = "cp-123456789abc4def8123456789abcdef"
        def handle(request):
            self.calls.append(request)
            return httpx.Response(200, json={"barrier": {"phase": "reserved"}})
        self.client = SandboxdClient("http://sandboxd.test", "synthetic-client-token")
        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.addAsyncCleanup(self.client.close)

    async def test_fixed_actions_scope_timeout_and_no_implicit_retry(self):
        for action in ("reserve", "start", "wait", "abort", "finish"):
            result = await self.client.workspace_backup(action, self.binding, self.cp, "backup-a", timeout=.25)
            self.assertEqual(result.status_code, 200)
            request = self.calls[-1]
            self.assertEqual(request.url.path, "/api/v1/workspaces/" + self.binding["workspaceId"] + "/backup/" + action)
            self.assertEqual(json.loads(request.content), {"binding": self.binding, "checkpointId": self.cp, "jobId": "backup-a", "timeoutMs": 250})
            self.assertEqual(request.extensions["timeout"]["read"], 35 if action in {"abort", "finish"} else 2.25)
        await self.client.get_workspace_backup(self.binding["workspaceId"])
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(self.calls[-1].method, "GET")

    async def test_invalid_actions_ids_and_limits_never_send_http(self):
        for action in ("shell", "unlock", "forget", "restore"):
            with self.assertRaises(ValueError): await self.client.workspace_backup(action, self.binding, self.cp, "backup-a")
        for timeout in (0, -1, 91, float("nan"), True):
            with self.assertRaises(ValueError): await self.client.workspace_backup("wait", self.binding, self.cp, "backup-a", timeout=timeout)
        with self.assertRaises(ValueError): await self.client.workspace_backup("start", self.binding, "latest", "backup-a")
        with self.assertRaises(ValueError): await self.client.get_workspace_backup("../escape")
        self.assertEqual(self.calls, [])

    async def test_transport_uncertainty_not_retried(self):
        count = 0
        def lost(request):
            nonlocal count
            count += 1
            raise httpx.ReadTimeout("synthetic response loss", request=request)
        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(transport=httpx.MockTransport(lost))
        with self.assertRaises(httpx.ReadTimeout): await self.client.workspace_backup("start", self.binding, self.cp, "backup-a")
        self.assertEqual(count, 1)
