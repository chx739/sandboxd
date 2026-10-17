from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentd.app.runtime.durable import (
    DurableJournal, JournalCorrupt, JournalError, JournalInUse,
)


class DurableJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="sandboxd-journal-", dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "control" / "tasks.jsonl"

    def test_append_reopen_and_independent_projection(self) -> None:
        data = {"taskId": "task-demo", "used": 1}
        with DurableJournal(self.path) as journal:
            saved = journal.append("task.created", data)
            data["used"] = 99
            saved["data"]["used"] = 88
            journal.entries()[0]["data"]["used"] = 77
            self.assertEqual(journal.entries()[0]["data"]["used"], 1)
        with DurableJournal(self.path) as journal:
            self.assertEqual(journal.entries()[0]["seq"], 1)
            self.assertEqual(journal.entries()[0]["data"]["used"], 1)
            self.assertEqual(journal.append("task.started", {})["seq"], 2)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_duplicate_owner_is_rejected_until_close(self) -> None:
        with DurableJournal(self.path):
            with self.assertRaises(JournalInUse):
                DurableJournal(self.path)
        with DurableJournal(self.path) as journal:
            self.assertEqual(journal.entries(), [])

    def test_partial_tail_is_not_a_record_and_is_repaired_before_append(self) -> None:
        with DurableJournal(self.path) as journal:
            journal.append("task.created", {"id": "one"})
        with self.path.open("ab") as handle:
            handle.write(b'{"seq":2,"data":')
        with DurableJournal(self.path) as journal:
            self.assertEqual(len(journal.entries()), 1)
            journal.append("task.started", {"id": "one"})
        with DurableJournal(self.path) as journal:
            self.assertEqual([r["seq"] for r in journal.entries()], [1, 2])

    def test_complete_corrupt_line_is_not_skipped(self) -> None:
        with DurableJournal(self.path) as journal:
            journal.append("task.created", {"used": 1})
        record = json.loads(self.path.read_text())
        record["data"]["used"] = 0
        self.path.write_text(json.dumps(record) + "\n")
        with self.assertRaises(JournalCorrupt):
            DurableJournal(self.path)
        # 启动失败也必须释放锁，不能把错误变成永久“有 owner”。
        with self.assertRaises(JournalCorrupt):
            DurableJournal(self.path)

    def test_invalid_or_large_event_does_not_change_file(self) -> None:
        with DurableJournal(self.path, max_record_bytes=512) as journal:
            before = self.path.read_bytes()
            for kind, data in [
                ("bad event", {}), ("task.created", {"x": float("nan")}),
                ("task.created", {"x": "a" * 1024}),
            ]:
                with self.assertRaises(ValueError):
                    journal.append(kind, data)
                self.assertEqual(self.path.read_bytes(), before)

    def test_symlink_journal_is_rejected(self) -> None:
        self.path.parent.mkdir(parents=True)
        target = self.path.parent / "target"
        target.write_text("untouched")
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            DurableJournal(self.path)
        self.assertEqual(target.read_text(), "untouched")

    def test_fsync_failure_poisons_writer(self) -> None:
        with DurableJournal(self.path) as journal:
            with patch("agentd.app.runtime.durable.os.fsync", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    journal.append("task.created", {})
            with self.assertRaises(JournalError):
                journal.append("task.started", {})

    def test_sequence_tamper_is_rejected_even_with_recomputed_checksum(self) -> None:
        import hashlib
        from agentd.app.runtime.durable import _encode
        with DurableJournal(self.path) as journal:
            journal.append("task.created", {})
        record = json.loads(self.path.read_text())
        record.pop("sha256")
        record["seq"] = 2
        record["sha256"] = hashlib.sha256(_encode(record)).hexdigest()
        self.path.write_bytes(_encode(record) + b"\n")
        with self.assertRaises(JournalCorrupt):
            DurableJournal(self.path)
