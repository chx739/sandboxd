"""合成控制日志；不表示真实restic删除或prune已经执行。"""
from pathlib import Path
import tempfile
import unittest

from langchain_core.messages import HumanMessage, AIMessage
from agentd.app.models import AlertEvent
from agentd.app.runtime.checkpoints import CheckpointLedger, CheckpointConflict
from agentd.app.runtime.durable import JournalCorrupt, JournalInUse, JournalError
from agentd.app.runtime.recovery import new_id
from agentd.app.runtime.session import SessionJournal
from agentd.app.runtime.session_refs import SessionReferences
from agentd.checkpoint_admin import perform


class RetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp"); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.refs = SessionReferences(self.root / "checkpoints")
        self.path = self.root / "checkpoints/checkpoints.jsonl"
        self.ledger = CheckpointLedger(self.path, self.refs)
        self.addCleanup(lambda: self.ledger.close())
        session = SessionJournal(self.root / "sessions", "session-0123456789abcdef")
        await session.initialize("task-retention", AlertEvent(labels={"cluster": "synthetic"}))
        node = await session.append_transcript("task-retention", [HumanMessage(content="synthetic"), AIMessage(content="done")])
        self.identity = dict(sessionId=session.session_id, branchId=new_id("branch"), sessionNodeId=node,
            workspaceId=new_id("workspace"), taskId="task-retention", attemptId=new_id("attempt"),
            sandboxId="sandbox-retention", podUid="pod-retention", generation=1,
            imageRef="sandboxd-workspace:runtime-v1.5", imageId="sha256:" + "4" * 64,
            sessionRef=await self.refs.capture(session, node))
        self.data = dict(repositoryId="repo-synthetic", repositoryCryptoId="1" * 64, snapshotId="2" * 64, manifestSha256="3" * 64)
        self.first = self.add_checkpoint(self.identity, self.data, None)

    def add_checkpoint(self, identity, data, parent):
        cp = new_id("cp")
        identity = dict(identity, taskId=new_id("task"))  # 不重复完整边界幂等键。
        self.ledger.prepare(cp, identity, parent); self.ledger.data_ready(cp, data); self.ledger.commit(cp)
        return cp

    def advance(self):
        return self.add_checkpoint(self.identity, dict(self.data, snapshotId="5" * 64), self.first)

    def reopen(self):
        self.ledger.close(); self.ledger = CheckpointLedger(self.path, self.refs)

    def target(self):
        return next(v for v in self.ledger.cleanup_preview()["snapshots"] if v["snapshotId"] == self.data["snapshotId"])

    async def test_heads_explicit_pins_and_stale_preview(self):
        self.assertFalse(self.target()["eligible"])
        self.advance(); preview = self.ledger.cleanup_preview()
        self.assertTrue(self.target()["eligible"])
        self.ledger.retain(self.first, True)
        before = self.path.read_bytes(); self.ledger.retain(self.first, True)
        self.assertEqual(before, self.path.read_bytes())
        with self.assertRaises(CheckpointConflict):
            self.ledger.prepare_forget(new_id("gc"), "1" * 64, "2" * 64, preview["planSha256"])
        self.reopen(); self.assertFalse(self.target()["eligible"])
        self.ledger.retain(self.first, False); self.assertTrue(self.target()["eligible"])

    async def test_snapshot_alias_protected_across_logical_repository_names(self):
        self.advance()
        self.add_checkpoint(dict(self.identity, branchId=new_id("branch"), workspaceId=new_id("workspace")),
                            dict(self.data, repositoryId="repo-alias"), None)
        group = self.target()
        self.assertEqual(len(group["checkpointIds"]), 2)
        self.assertFalse(group["eligible"])
        self.assertEqual(group["repositoryIds"], ["repo-alias", "repo-synthetic"])

    async def test_restore_and_uncommitted_source_protection(self):
        self.advance()
        request = self.ledger.prepare_restore(new_id("restore"), self.first, session_id=self.identity["sessionId"],
            branch_id=new_id("branch"), workspace_id=new_id("workspace"), task_id=new_id("task"))
        self.assertFalse(self.target()["eligible"])
        self.ledger.advance_restore(request["restoreId"], "FAILED", "backup_failed")
        self.reopen(); self.assertFalse(self.target()["eligible"])
        self.assertIn("restore-source:" + request["restoreId"], self.ledger.protected_checkpoints()[self.first])

    async def test_forget_tombstone_rebuild_no_restore_and_no_new_save(self):
        head = self.advance(); view = self.ledger.cleanup_preview(); gc = new_id("gc")
        operation = self.ledger.prepare_forget(gc, "1" * 64, "2" * 64, view["planSha256"])
        before = self.path.read_bytes()
        self.assertEqual(operation, self.ledger.prepare_forget(gc, "1" * 64, "2" * 64, view["planSha256"]))
        self.assertEqual(before, self.path.read_bytes())
        self.reopen(); self.assertEqual(self.ledger.file_availability(self.first), "forget-pending")
        with self.assertRaises(CheckpointConflict): self.ledger.restore_point(self.first)
        with self.assertRaises(CheckpointConflict): self.ledger.prepare(new_id("cp"), dict(self.identity, taskId=new_id("task")), head)
        self.ledger.complete_forget(gc); self.reopen()
        self.assertEqual(self.ledger.file_availability(self.first), "forgotten")
        self.assertEqual(self.ledger.get(self.first)["status"], "COMMITTED")
        self.assertEqual(self.ledger.restore_point(head)[0]["checkpointId"], head)
        self.assertNotIn(self.first, self.ledger.committed_at_node(self.identity["sessionId"], self.identity["sessionNodeId"]))

    async def test_preparing_and_bad_session_ref_block_preview(self):
        head = self.advance()
        cp = new_id("cp"); self.ledger.prepare(cp, dict(self.identity, taskId=new_id("task")), head)
        self.assertFalse(self.target()["eligible"])
        self.ledger.fail(cp, "backup_failed")
        path = self.refs.directory / (self.identity["sessionRef"]["sha256"] + ".json")
        path.write_bytes(b"corrupt immutable fixture")
        self.assertFalse(self.target()["eligible"])

    async def test_invalid_retention_replay_refused(self):
        self.ledger._journal.append("checkpoint.pin", {"checkpointId": self.first, "retained": "true"})
        self.ledger.close()
        with self.assertRaises(JournalCorrupt): CheckpointLedger(self.path, self.refs)

    async def test_corrupt_forget_cannot_delete_head_even_with_valid_checksum(self):
        view = self.ledger.cleanup_preview()
        self.ledger._journal.append("checkpoint.forget", {"schemaVersion": 1, "gcId": new_id("gc"),
            "repositoryCryptoId": "1" * 64, "snapshotId": "2" * 64, "checkpointIds": [self.first],
            "planSha256": view["planSha256"], "status": "PREPARED"})
        self.ledger.close()
        with self.assertRaises(JournalCorrupt): CheckpointLedger(self.path, self.refs)

    async def test_failed_uncommitted_alias_and_other_crypto_repository(self):
        head = self.advance()
        pending = new_id("cp")
        self.ledger.prepare(pending, dict(self.identity, taskId=new_id("task")), head)
        self.ledger.data_ready(pending, self.data); self.ledger.fail(pending, "backup_failed")
        self.assertFalse(self.target()["eligible"])
        self.assertTrue(any("uncommitted-data" in r for r in self.target()["reasons"]))
        self.add_checkpoint(dict(self.identity, branchId=new_id("branch"), workspaceId=new_id("workspace")),
                            dict(self.data, repositoryCryptoId="6" * 64), None)
        group = next(r for r in self.ledger.cleanup_preview()["snapshots"] if r["repositoryCryptoId"] == "6" * 64)
        self.assertEqual(len(group["checkpointIds"]), 1)

    async def test_entire_unprotected_alias_group_becomes_unavailable(self):
        self.advance()
        other = dict(self.identity, branchId=new_id("branch"), workspaceId=new_id("workspace"))
        alias = self.add_checkpoint(other, dict(self.data, repositoryId="repo-alias"), None)
        self.add_checkpoint(other, dict(self.data, snapshotId="7" * 64), alias)
        view = self.ledger.cleanup_preview(); gc = new_id("gc")
        record = self.ledger.prepare_forget(gc, "1" * 64, "2" * 64, view["planSha256"])
        self.assertEqual(record["checkpointIds"], sorted([self.first, alias]))
        self.reopen()
        for cp in (self.first, alias):
            with self.assertRaises(CheckpointConflict): self.ledger.restore_point(cp)
        self.assertEqual(self.ledger.forget_request(gc), record)
        self.assertEqual(self.ledger.forget_requests(), [record])

    async def test_admin_preview_readonly_and_live_writer_refused(self):
        with self.assertRaises(JournalInUse): perform(self.path.parent, "preview")
        self.ledger.close(); before = self.path.read_bytes()
        before_stat = self.path.stat()
        self.assertIn("planSha256", perform(self.path.parent, "preview"))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(before_stat.st_ctime_ns, self.path.stat().st_ctime_ns)
        with self.assertRaises(ValueError): perform(self.root / "missing", "preview")
        reader = CheckpointLedger(self.path, self.refs, read_only=True)
        try:
            with self.assertRaises(JournalError): reader.retain(self.first, True)
        finally:
            reader.close()
