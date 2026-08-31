from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agentd.app.clients import LinuxHostClient, load_linux_targets


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(b"Linux demo 6.8\n")
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.killed = False

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


class LinuxHostClientTest(unittest.IsolatedAsyncioTestCase):
    def _config(self, directory: Path) -> Path:
        identity = directory / "id_ed25519"
        known_hosts = directory / "known_hosts"
        identity.write_text("private", encoding="utf-8")
        known_hosts.write_text("host key", encoding="utf-8")
        identity.chmod(0o600)
        known_hosts.chmod(0o600)
        config = directory / "targets.json"
        config.write_text(
            json.dumps(
                {
                    "targets": [
                        {
                            "targetId": "demo-linux",
                            "host": "127.0.0.1",
                            "port": 2222,
                            "user": "agentdemo",
                            "identityFile": str(identity),
                            "knownHostsFile": str(known_hosts),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        config.chmod(0o600)
        return config

    async def test_fixed_argv_and_connector_policy(self) -> None:
        with TemporaryDirectory(dir="/tmp") as temporary:
            client = LinuxHostClient.from_file(self._config(Path(temporary)))
            unknown = await client.read("missing", "host_summary")
            self.assertEqual(unknown.status_code, 403)
            self.assertEqual(unknown.body["denyLayer"], "connector-policy")
            denied = await client.read("demo-linux", "rm -rf /")
            self.assertEqual(denied.status_code, 403)

            captured: tuple[object, ...] = ()

            async def fake_exec(*argv: object, **kwargs: object) -> _FakeProcess:
                nonlocal captured
                captured = argv
                self.assertNotIn("shell", kwargs)
                return _FakeProcess()

            with patch("asyncio.create_subprocess_exec", new=fake_exec):
                result = await client.read("demo-linux", "host_summary")

            self.assertEqual(result.status_code, 200)
            self.assertEqual(captured[0], "/usr/bin/ssh")
            self.assertIn("StrictHostKeyChecking=yes", captured)
            self.assertIn("ClearAllForwardings=yes", captured)
            self.assertEqual(captured[-1], "host_summary")
            self.assertNotIn("rm -rf /", captured)

    async def test_config_and_key_permissions_fail_closed(self) -> None:
        with TemporaryDirectory(dir="/tmp") as temporary:
            config = self._config(Path(temporary))
            config.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "0600"):
                load_linux_targets(config)


if __name__ == "__main__":
    unittest.main()
