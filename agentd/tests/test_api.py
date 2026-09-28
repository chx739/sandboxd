from __future__ import annotations

import unittest
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from httpx import ASGITransport, AsyncClient

from agentd.app.config import Settings
from agentd.app.main import create_app


class AgentAPIAuthTest(unittest.IsolatedAsyncioTestCase):
    async def test_alert_webhook_requires_its_own_token(self) -> None:
        project = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as trace_dir:
            session_dir = Path(trace_dir) / "sessions"
            session_dir.mkdir()
            demo_id = "session-0123456789abcdef"
            copyfile(
                project / "testdata" / "session-tree-demo" / (demo_id + ".jsonl"),
                session_dir / (demo_id + ".jsonl"),
            )
            settings = Settings(
                listen_host="127.0.0.1",
                listen_port=8090,
                api_token="api-token",
                alert_token="alert-token",
                prometheus_url="http://127.0.0.1:9090",
                sandboxd_url="http://127.0.0.1:8080",
                sandboxd_token="sandbox-token",
                llm_mode="replay",
                llm_base_url="",
                llm_model="",
                llm_api_key="",
                llm_thinking="default",
                replay_file=project
                / "testdata"
                / "injection-denied.replay.json",
                trace_dir=Path(trace_dir),
            )
            async with AsyncClient(
                transport=ASGITransport(app=create_app(settings)),
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/api/v1/alerts",
                    json={"status": "resolved", "alerts": []},
                )
                self.assertEqual(response.status_code, 401)

                response = await client.post(
                    "/api/v1/alerts",
                    headers={"Authorization": "Bearer alert-token"},
                    json={"status": "resolved", "alerts": []},
                )
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()["taskIds"], [])

                response = await client.get(
                    "/api/v1/tasks/missing",
                    headers={"Authorization": "Bearer alert-token"},
                )
                self.assertEqual(response.status_code, 401)

                response = await client.get(
                    "/api/v1/tasks",
                    headers={"Authorization": "Bearer alert-token"},
                )
                self.assertEqual(response.status_code, 401)

                response = await client.get(
                    "/api/v1/tasks",
                    headers={"Authorization": "Bearer api-token"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"tasks": []})

                response = await client.get(
                    "/api/v1/plugins",
                    headers={"Authorization": "Bearer alert-token"},
                )
                self.assertEqual(response.status_code, 401)

                response = await client.get(
                    "/api/v1/plugins",
                    headers={"Authorization": "Bearer api-token"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [item["id"] for item in response.json()["plugins"]],
                    ["prometheus", "kubernetes", "linux-host", "files"],
                )

                # 运行控制和 Session 管理只能使用 API Token，告警入口 Token
                # 不能转向、追加或取消 Agent。
                response = await client.post(
                    "/api/v1/tasks/missing/steer",
                    headers={"Authorization": "Bearer alert-token"},
                    json={"content": "change direction"},
                )
                self.assertEqual(response.status_code, 401)

                response = await client.post(
                    "/api/v1/tasks/missing/steer",
                    headers={"Authorization": "Bearer api-token"},
                    json={"content": "change direction"},
                )
                self.assertEqual(response.status_code, 404)

                response = await client.post(
                    "/api/v1/tasks/missing/cancel",
                    headers={"Authorization": "Bearer api-token"},
                )
                self.assertEqual(response.status_code, 404)

                response = await client.get(
                    "/api/v1/sessions/session-ffffffffffffffff",
                    headers={"Authorization": "Bearer api-token"},
                )
                self.assertEqual(response.status_code, 404)

                response = await client.get(
                    f"/api/v1/sessions/{demo_id}/tree",
                    headers={"Authorization": "Bearer alert-token"},
                )
                self.assertEqual(response.status_code, 401)
                response = await client.get(
                    f"/api/v1/sessions/{demo_id}/tree",
                    headers={"Authorization": "Bearer api-token"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()["nodes"]), 7)
                response = await client.get(
                    f"/api/v1/sessions/{demo_id}/path/node-0000000000000005",
                    headers={"Authorization": "Bearer api-token"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["messages"][-1]["content"], "Old ending")
                response = await client.post(
                    f"/api/v1/sessions/{demo_id}/branch/node-0000000000000003",
                    headers={"Authorization": "Bearer api-token"},
                )
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()["branchedFrom"], "node-0000000000000003")


if __name__ == "__main__":
    unittest.main()
