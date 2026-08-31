from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from agentd.app.config import Settings
from agentd.app.main import create_app


class AgentAPIAuthTest(unittest.TestCase):
    def test_alert_webhook_requires_its_own_token(self) -> None:
        project = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as trace_dir:
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
            with TestClient(create_app(settings)) as client:
                response = client.post(
                    "/api/v1/alerts",
                    json={"status": "resolved", "alerts": []},
                )
                self.assertEqual(response.status_code, 401)

                response = client.post(
                    "/api/v1/alerts",
                    headers={"Authorization": "Bearer alert-token"},
                    json={"status": "resolved", "alerts": []},
                )
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()["taskIds"], [])

                response = client.get(
                    "/api/v1/tasks/missing",
                    headers={"Authorization": "Bearer alert-token"},
                )
                self.assertEqual(response.status_code, 401)

                response = client.get(
                    "/api/v1/tasks",
                    headers={"Authorization": "Bearer alert-token"},
                )
                self.assertEqual(response.status_code, 401)

                response = client.get(
                    "/api/v1/tasks",
                    headers={"Authorization": "Bearer api-token"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"tasks": []})

                response = client.get(
                    "/api/v1/plugins",
                    headers={"Authorization": "Bearer alert-token"},
                )
                self.assertEqual(response.status_code, 401)

                response = client.get(
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
                response = client.post(
                    "/api/v1/tasks/missing/steer",
                    headers={"Authorization": "Bearer alert-token"},
                    json={"content": "change direction"},
                )
                self.assertEqual(response.status_code, 401)

                response = client.post(
                    "/api/v1/tasks/missing/steer",
                    headers={"Authorization": "Bearer api-token"},
                    json={"content": "change direction"},
                )
                self.assertEqual(response.status_code, 404)

                response = client.post(
                    "/api/v1/tasks/missing/cancel",
                    headers={"Authorization": "Bearer api-token"},
                )
                self.assertEqual(response.status_code, 404)

                response = client.get(
                    "/api/v1/sessions/session-0123456789abcdef",
                    headers={"Authorization": "Bearer api-token"},
                )
                self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
