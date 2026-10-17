from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from agentd.app.config import load_settings


class RecoveryConfigTests(unittest.TestCase):
    def environment(self, **extra):
        return patch.dict(os.environ, {"AGENTD_TOKEN": "synthetic-agent", "AGENTD_ALERT_TOKEN": "synthetic-alert",
                                       "SANDBOXD_TOKEN": "synthetic-sandbox", **extra}, clear=True)

    def test_deployment_entry_defaults_to_nfs_and_native_control_directory(self):
        with self.environment():
            settings = load_settings()
        self.assertEqual(settings.workspace_backend, "nfs")
        self.assertEqual(settings.recovery_dir, Path.home() / ".local/share/sandboxd/agent-control")

    def test_legacy_offline_backend_is_explicit_and_has_no_recovery_directory(self):
        with self.environment(AGENTD_WORKSPACE_BACKEND="local"):
            settings = load_settings()
        self.assertEqual(settings.workspace_backend, "local")
        self.assertIsNone(settings.recovery_dir)

    def test_temporary_or_relative_deployment_control_directory_is_rejected(self):
        for directory in ("relative/control", "/tmp/agent-control", "/run/user/agent-control", "/var/tmp/control", "/dev/shm/control"):
            with self.subTest(directory=directory), self.environment(AGENTD_RECOVERY_DIR=directory):
                with self.assertRaises(ValueError):
                    load_settings()
