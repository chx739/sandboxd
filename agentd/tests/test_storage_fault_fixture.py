"""合成夹具单测；真实服务成绩单列。"""
import json
import unittest
from unittest.mock import Mock, patch

from agentd.workspace_storage_fault_demo import MINIO, MinioOutage


class StorageFaultFixtureTests(unittest.TestCase):
    def info(self, **state):
        return json.dumps([{"Id": "fixed-container-id", "Name": "/" + MINIO,
            "Config": {"Labels": {"com.docker.compose.project": "sandboxd-workspace-backup"}},
            "State": dict(Running=True, Paused=False, **state)}])

    def test_untrusted_or_prepaused_service_is_untouched(self):
        for raw in (self.info().replace("sandboxd-workspace-backup\"", "other-project\""),
                    self.info().replace('"Paused": false', '"Paused": true')):
            with patch("agentd.workspace_storage_fault_demo.checked", return_value=raw) as checked, \
                    patch("agentd.workspace_storage_fault_demo.subprocess.Popen") as launch:
                with self.assertRaises(ValueError):
                    MinioOutage().begin()
                launch.assert_not_called()
                self.assertEqual(checked.call_count, 1)

    def test_watchdog_precedes_pause_and_targets_exact_id(self):
        outage = MinioOutage()
        calls = []
        def checked(args):
            calls.append(args)
            if args[1] == "pause":
                self.assertIsNotNone(outage.watchdog)
                return "fixed-container-id"
            return self.info().replace('"Paused": false', '"Paused": true') if len(calls) > 1 else self.info()
        with patch("agentd.workspace_storage_fault_demo.checked", side_effect=checked), \
                patch("agentd.workspace_storage_fault_demo.subprocess.Popen") as launch:
            outage.begin()
            self.assertEqual(calls[1], ["docker", "pause", "fixed-container-id"])
            self.assertEqual(launch.call_args.args[0][-1], "fixed-container-id")
            self.assertTrue(launch.call_args.kwargs["start_new_session"])

    def test_failed_unpause_keeps_watchdog(self):
        outage = MinioOutage()
        outage.container_id, outage.watchdog = "fixed-container-id", Mock()
        paused = self.info().replace('"Paused": false', '"Paused": true')
        with patch("agentd.workspace_storage_fault_demo.checked", side_effect=[paused, RuntimeError("unpause failed")]):
            with self.assertRaises(RuntimeError):
                outage.end()
        outage.watchdog.terminate.assert_not_called()

    def test_still_paused_after_unpause_keeps_watchdog(self):
        outage = MinioOutage()
        outage.container_id, outage.watchdog = "fixed-container-id", Mock()
        paused = self.info().replace('"Paused": false', '"Paused": true')
        with patch("agentd.workspace_storage_fault_demo.checked", side_effect=[paused, "", paused]):
            with self.assertRaises(AssertionError):
                outage.end()
        outage.watchdog.terminate.assert_not_called()
