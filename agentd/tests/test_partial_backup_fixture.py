"""管理故障夹具契约；真实exit3与孤立快照由独立服务实验验证。"""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from agentd.workspace_partial_backup import PartialBackup, READABLE


class PartialBackupFixtureTests(unittest.IsolatedAsyncioTestCase):
    def bare(self):
        v = object.__new__(PartialBackup)
        v.mode_dirty = True
        v.original_mode, v.container_id, v.path = "600", "owned-nfs-id", "/export/workspaces/workspace-test/report.md"
        v.watchdog, v.minio = Mock(), Mock()
        v.nfs_container_id, v.nfs_path = "owned-nfs-id", "/export/workspaces/workspace-test/report.md"
        v.mode = Mock(return_value="0")
        v.observed_snapshots_before_chmod = True
        v.checkpoint_id, v.workspace_id = "cp-fixed", "workspace-test"
        v.expected_sha256 = hashlib.sha256(b"synthetic report\n").hexdigest()
        v.previous_snapshot_id = "b" * 64
        v.repository = Mock()
        v.pod_read_denied, v.mode_at_failure = True, "000"
        return v

    def test_arbitrary_root_rejected_before_any_service_operation(self):
        with self.assertRaises(ValueError):
            PartialBackup(Path("/tmp/arbitrary"), "restic-" + "a" * 16, "172.23.0.4", Mock(), "fixture")

    def test_failed_mode_restore_keeps_watchdog_and_dirty_flag(self):
        v = self.bare()
        with patch("agentd.workspace_partial_backup.subprocess.run", return_value=Mock(returncode=0)), \
                patch("agentd.workspace_partial_backup.checked", side_effect=RuntimeError("not confirmed")):
            with self.assertRaises(RuntimeError):
                v.restore_mode()
        self.assertTrue(v.mode_dirty)
        v.watchdog.terminate.assert_not_called()

    def test_partial_snapshot_contains_readable_only_and_old_snapshot_readable(self):
        v = self.bare()
        v.mode_dirty = False
        v.repository.execute.side_effect = [json.dumps([{"id": "a" * 64}]),
            json.dumps({"struct_type": "node", "type": "file", "path": "/workspace/evidence.txt"}), READABLE, "synthetic report\n"]
        self.assertTrue(v.verify_partial()["unreadableReportAbsent"])
        self.assertEqual(v.repository.execute.call_args.args, ("dump", "b" * 64, "/workspace/report.md"))

    def test_snapshot_with_report_is_not_accepted_as_partial_proof(self):
        v = self.bare()
        v.mode_dirty = False
        v.repository.execute.side_effect = [json.dumps([{"id": "a" * 64}]), "\n".join(json.dumps(
            {"struct_type": "node", "type": "file", "path": "/workspace/" + p}) for p in ["evidence.txt", "report.md"])]
        with self.assertRaises(AssertionError):
            v.verify_partial()

    def test_missing_pod_mount_restores_retained_backend_file(self):
        v = self.bare()
        with patch("agentd.workspace_partial_backup.subprocess.run", return_value=Mock(returncode=1)), \
                patch("agentd.workspace_partial_backup.checked", side_effect=["", "600"]) as checked:
            v.restore_mode()
        self.assertEqual(checked.call_args_list[0].args[0][-3:], ["chmod", "600", v.nfs_path])
        self.assertFalse(v.mode_dirty)
        self.assertIsNone(v.watchdog)

    def test_unconfirmed_restored_mode_keeps_watchdog(self):
        v = self.bare()
        with patch("agentd.workspace_partial_backup.subprocess.run", return_value=Mock(returncode=0)), \
                patch("agentd.workspace_partial_backup.checked"):
            with self.assertRaises(AssertionError):
                v.restore_mode()
        self.assertTrue(v.mode_dirty)
        v.watchdog.terminate.assert_not_called()

    async def test_permission_change_follows_observed_snapshot_subcommand(self):
        v = self.bare()
        barrier = {"checkpointId": v.checkpoint_id, "access": {"workspaceId": v.workspace_id}, "podName": "owned-pod"}
        with patch("agentd.workspace_partial_backup.subprocess.run", side_effect=[Mock(returncode=0, stdout="PID COMMAND\n17 restic\n"),
                Mock(returncode=0, stdout="/usr/local/bin/restic\0snapshots\0--tag\0checkpoint:cp-fixed\0"),
                Mock(returncode=1, stdout="", stderr="Permission denied"), Mock(returncode=0, stdout=READABLE)]), \
                patch("agentd.workspace_partial_backup.subprocess.Popen") as launch, \
                patch("agentd.workspace_partial_backup.checked") as checked:
            await v.after_start(barrier)
        launch.assert_called_once()
        self.assertEqual(checked.call_args.args[0][-3:], ["chmod", "000", v.path])
        v.minio.end.assert_called_once()
