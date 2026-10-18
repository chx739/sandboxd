"""验收夹具的固定输入及重复请求观察契约；不当真实服务成绩。"""
import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from agentd.workspace_checkpoint_acceptance import (
    RepeatedSaveCoordinator, replace_bytes, source_from, source_with_fault,
)
from agentd.app.runtime.checkpoint_coordinator import CheckpointCoordinator


SOURCE = {"snapshotId": "a" * 64, "manifestSha256": "b" * 64, "repositoryCryptoId": "c" * 64,
          "imageRef": "sandboxd-workspace:runtime-v1.5", "imageId": "sha256:" + "d" * 64}


class AcceptanceInputTests(unittest.TestCase):
    def test_snapshot_fault_changes_only_snapshot_and_keeps_full_hash(self):
        result = source_with_fault(SOURCE, "snapshot-missing")
        self.assertNotEqual(result["snapshotId"], SOURCE["snapshotId"])
        self.assertEqual(len(result["snapshotId"]), 64)
        self.assertEqual({k: v for k, v in result.items() if k != "snapshotId"}, {k: v for k, v in SOURCE.items() if k != "snapshotId"})

    def test_manifest_and_image_faults_do_not_change_snapshot_or_repository(self):
        for kind, field in (("manifest-mismatch", "manifestSha256"), ("image-id-mismatch", "imageId")):
            result = source_with_fault(SOURCE, kind)
            self.assertEqual({k for k in SOURCE if SOURCE[k] != result[k]}, {field})
        self.assertEqual(SOURCE["imageId"], "sha256:"+"d"*64)

    def test_no_free_case_or_source_field(self):
        with self.assertRaises(ValueError): source_with_fault(SOURCE, "arbitrary")
        cp = {"data": {k: SOURCE[k] for k in ("snapshotId", "manifestSha256", "repositoryCryptoId")},
              "identity": {k: SOURCE[k] for k in ("imageRef", "imageId")}}
        self.assertEqual(source_from(cp), SOURCE)

    def test_reference_fault_can_restore_exact_original_bytes_and_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"ref.json"
            original = b'{"fixed": "original", "sequence": [1, 2]}\n'
            replace_bytes(path, original)
            replace_bytes(path, b'{"syntheticCorruption":true}\n')
            replace_bytes(path, original)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [path])


class RepeatedSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeat_same_request_records_only_finish_and_preserves_journals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); go = root/"go"; cp = root/"cp"
            go.write_bytes(b"synthetic-go\n"); cp.write_bytes(b"synthetic-checkpoint\n")
            client = SimpleNamespace(backup_calls=[])
            ledger = SimpleNamespace(_journal=SimpleNamespace(path=cp))
            coordinator = RepeatedSaveCoordinator(client, ledger, None, "repo-test", go_journal=go)
            coordinator.release.set()
            result = {"checkpointId": "cp-12345678123442348abc123456789abc", "revision": 1, "data": {"snapshotId": "a"*64}}
            calls = []
            async def save(*args):
                calls.append(args)
                if len(calls) == 2:
                    client.backup_calls.append({"action": "finish", "checkpointId": result["checkpointId"],
                        "jobId": "backup-"+result["checkpointId"][3:], "statusCode": 200})
                return copy.deepcopy(result)
            with patch.object(CheckpointCoordinator, "save", save):
                self.assertEqual(await coordinator.save("recovery", "journal", "binding", "node"), result)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0], calls[1])
            self.assertTrue(coordinator.saved.is_set())
            self.assertTrue(coordinator.repeat["sameResponse"])
            self.assertEqual(go.read_bytes(), b"synthetic-go\n")
            self.assertEqual(cp.read_bytes(), b"synthetic-checkpoint\n")
