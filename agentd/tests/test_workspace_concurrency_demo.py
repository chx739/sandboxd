from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentd.workspace_concurrency_demo import fixture
from agentd.workspace_process_demo import start_agent
from agentd.workspace_unavailable_demo import watchdog_argv


class WorkspaceConcurrencyFixtureTests(unittest.TestCase):
    def test_replay_is_bounded_and_only_writes_one_guard(self):
        replay = fixture()
        self.assertEqual(replay["kind"], "deterministic-policy-case")
        responses = replay["responses"]
        self.assertEqual(len(responses), 3)
        tools = [c["name"] for r in responses for c in r.get("toolCalls", [])]
        self.assertEqual(tools, ["write_file", "read_file"])
        self.assertEqual(responses[1]["delayMs"], 15000)
        self.assertEqual(responses[2]["delayMs"], 60000)

    def test_duplicate_process_uses_different_local_port_without_live_credentials(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            with patch.dict("os.environ", {"SANDBOXD_OPERATOR_TOKEN": "synthetic-excluded", "OPENAI_API_KEY": "synthetic-excluded"}):
                with patch("agentd.workspace_process_demo.subprocess.Popen", return_value=SimpleNamespace(pid=42)) as popen:
                    with contextlib.redirect_stdout(io.StringIO()):
                        _, log = start_agent(root, "synthetic-go", "synthetic-api", "synthetic-alert", 2, port=18091)
                    log.close()
            argv = popen.call_args.args[0]
            self.assertEqual(argv[argv.index("--port") + 1], "18091")
            self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")
            env = popen.call_args.kwargs["env"]
            self.assertNotIn("SANDBOXD_OPERATOR_TOKEN", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertEqual(env["AGENTD_LLM_MODE"], "replay")
            self.assertEqual(env["AGENTD_RECOVERY_DIR"], str(root / "agent-control"))

    def test_unexpected_fixture_port_is_rejected_before_process_creation(self):
        with patch("agentd.workspace_process_demo.subprocess.Popen") as popen:
            for port in (0, 8090, 18092):
                with self.subTest(port=port), self.assertRaises(ValueError):
                    start_agent(Path("/tmp"), "unused", "unused", "unused", 1, port=port)
            popen.assert_not_called()

    def test_nfs_watchdog_only_targets_verified_container_id(self):
        argv = watchdog_argv("a" * 64)
        self.assertEqual(argv[1], "-c")
        self.assertIn("time.sleep(45)", argv[2])
        self.assertIn("['docker','unpause',", argv[2])
        self.assertIn(repr("a" * 64), argv[2])

    def test_nfs_watchdog_rejects_names_and_command_injection(self):
        for value in ("sandboxd-workspace-nfs-1", "a" * 63, "A" * 64, "a" * 63 + ";"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                watchdog_argv(value)
