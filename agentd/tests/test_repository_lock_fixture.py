"""真实锁夹具的范围/清理契约，单测不代表真实仓库锁成绩。"""
from pathlib import Path
import signal
import unittest
from unittest.mock import Mock, patch

from agentd.workspace_repository_lock import RepositoryLock


class RepositoryLockFixtureTests(unittest.TestCase):
    def bare(self):
        value = object.__new__(RepositoryLock)
        value.process, value.watchdog = Mock(), Mock()
        value.process.poll.return_value = None
        value.command, value.env = ["trusted-restic"], {}
        return value

    def test_arbitrary_control_root_rejected(self):
        with self.assertRaises(ValueError):
            RepositoryLock(Path("/tmp/arbitrary"), "restic-" + "a" * 16, "172.23.0.4")

    def test_only_normal_check_signal_shutdown_no_unlock(self):
        value = self.bare()
        value.locks = Mock(return_value=[])
        value.end()
        self.assertEqual([c.args for c in value.process.send_signal.call_args_list],
                         [(signal.SIGCONT,), (signal.SIGINT,)])
        value.process.wait.assert_called_once_with(timeout=15)
        value.watchdog.terminate.assert_called_once()

    def test_remaining_lock_never_force_removed(self):
        value = self.bare()
        value.locks = Mock(return_value=["a" * 64])
        with self.assertRaises(AssertionError):
            value.end()
        value.watchdog.terminate.assert_not_called()

    def test_backend_error_never_exposes_stderr(self):
        value = self.bare()
        with patch("agentd.workspace_repository_lock.subprocess.run", return_value=Mock(returncode=11, stderr="SECRET_CANARY")):
            with self.assertRaises(RuntimeError) as error:
                value.execute("list", "locks")
        self.assertNotIn("SECRET_CANARY", str(error.exception))
