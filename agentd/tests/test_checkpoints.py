from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

from agentd.app.models import AlertEvent
from agentd.app.runtime.checkpoints import CheckpointLedger, CheckpointConflict
from agentd.app.runtime.durable import JournalCorrupt
from agentd.app.runtime.recovery import RecoveryLedger, RecoveryConflict, RecoveryLimit, new_id
from agentd.app.runtime.session import SessionJournal
from agentd.app.runtime.session_refs import SessionReferences


class CheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp", prefix="sandboxd-checkpoint-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.refs = SessionReferences(self.root / "control")
        self.path = self.root / "control/checkpoints.jsonl"
        self.ledger = CheckpointLedger(self.path, self.refs)
        self.addCleanup(lambda: self.ledger.close())
        self.recovery = RecoveryLedger(self.root / "tasks/tasks.jsonl")
        self.addCleanup(self.recovery.close)
        self.now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.sid = "session-0123456789abcdef"
        self.task = "task-fixture"
        self.branch = new_id("branch")
        self.workspace = new_id("workspace")
        self.recovery.create(task_id=self.task, session_id=self.sid, branch_id=self.branch,
                             workspace_id=self.workspace, alert={"labels": {"cluster": "test"}},
                             deadline=self.now + timedelta(seconds=120))
        self.attempt = self.recovery.begin_attempt(self.task, self.now)["attempts"][-1]["attemptId"]
        self.recovery.bind(self.task, self.attempt, "sandbox-a", "pod-uid-a")
        self.session = SessionJournal(self.root / "sessions", self.sid).for_attempt(self.attempt)
        self.alert = AlertEvent(labels={"alertname": "HighCPU", "cluster": "test"})
        await self.session.initialize(self.task, self.alert)
        self.messages = [SystemMessage(content="safety"), HumanMessage(content="diagnose"),
                         AIMessage(content="", tool_calls=[{"id": "call-a", "name": "read_file", "args": {"path": "report.md"}}]),
                         ToolMessage(content='{"observation":"synthetic"}', tool_call_id="call-a")]
        self.node = await self.session.append_transcript(self.task, self.messages)
        self.ref = await self.refs.capture(self.session, self.node)
        self.recovery.complete_boundary(self.task, self.attempt, self.node, [])
        self.identity = {"sessionId": self.sid, "branchId": self.branch, "sessionNodeId": self.node,
                         "workspaceId": self.workspace, "taskId": self.task, "attemptId": self.attempt,
                         "sandboxId": "sandbox-a", "podUid": "pod-uid-a", "generation": 1,
                         "imageRef": "sandboxd-workspace:runtime-v1.5", "imageId": "sha256:" + "4" * 64,
                         "sessionRef": self.ref}
        self.owner = {"workspaceId": self.workspace, "sessionId": self.sid, "branchId": self.branch,
                      "generation": 1, "imageRef": self.identity["imageRef"], "imageId": self.identity["imageId"],
                      "phase": "bound", "owner": {"taskId": self.task, "attemptId": self.attempt,
                                                  "sandboxId": "sandbox-a", "podUid": "pod-uid-a",
                                                  "createSubmitted": True, "stopConfirmed": False}}
        self.data = {"repositoryId": "repo-fixture", "repositoryCryptoId": "1" * 64,
                     "snapshotId": "2" * 64, "manifestSha256": "3" * 64}
        self.cp = new_id("cp")

    def reopen(self):
        self.ledger.close()
        self.ledger = CheckpointLedger(self.path, self.refs)

    def ready(self):
        self.ledger.prepare(self.cp, self.identity, None)
        return self.ledger.data_ready(self.cp, self.data)

    def commit(self):
        return self.recovery.commit_checkpoint(self.ledger, self.cp, self.owner, self.now)

    async def test_prefix_immutable_complete_and_branch_isolated(self):
        before = self.refs.read(self.ref)
        self.messages += [AIMessage(content="later ending")]
        await self.session.append_transcript(self.task, self.messages)
        self.assertEqual(self.refs.read(self.ref), before)
        self.assertEqual(len(before["nodes"]), 4)
        self.assertNotIn("later ending", json.dumps(before))
        self.assertEqual(await self.refs.capture(self.session, self.node), self.ref)
        tree = await self.session.tree()
        partial = next(n["nodeId"] for n in tree["nodes"] if n["role"] == "assistant" and not n["branchable"])
        with self.assertRaises(ValueError):
            await self.refs.capture(self.session, partial)
        _, prefix, _ = await self.session.load_branch(self.node)
        other = self.session.for_attempt(new_id("attempt"))
        # Latest attempt cannot fork an old head through the automatic recovery path.
        with self.assertRaises(ValueError):
            await other.initialize(self.task, self.alert, self.node)
        self.assertEqual(self.refs.read(self.ref), before)
        # Offline Session树仍能另开消息分支；引用只捕获选定父链。
        sibling = SessionJournal(self.root / "sessions", self.sid)
        await sibling.initialize("task-sibling", self.alert, self.node)
        sibling_node = await sibling.append_transcript("task-sibling", prefix + [HumanMessage(content="other route"), AIMessage(content="sibling ending")])
        sibling_ref = await self.refs.capture(sibling, sibling_node)
        self.assertNotIn("later ending", json.dumps(self.refs.read(sibling_ref)))
        self.assertNotIn("sibling ending", json.dumps(self.refs.read(self.ref)))

    async def test_ref_corruption_missing_scope_symlink_and_escape_fail(self):
        self.ready()
        self.commit()
        target = self.root / "control" / self.ref["path"]
        original = target.read_bytes()
        target.write_bytes(original + b" ")
        with self.assertRaises(JournalCorrupt):
            self.ledger.restore_point(self.cp)
        target.write_bytes(original)
        wrong = dict(self.ref, nodeId="node-ffffffffffffffff")
        with self.assertRaises(JournalCorrupt):
            self.refs.read(wrong)
        wrong = dict(self.ref, path="../outside.json")
        with self.assertRaises(JournalCorrupt):
            self.refs.read(wrong)
        target.unlink()
        with self.assertRaises(FileNotFoundError):
            self.ledger.restore_point(self.cp)
        other = self.root / "other.json"; other.write_bytes(original)
        target.symlink_to(other)
        with self.assertRaises(OSError):
            self.refs.read(self.ref)

    async def test_prepare_data_ready_not_restore_points_after_restart(self):
        self.ledger.prepare(self.cp, self.identity, None)
        self.reopen()
        self.assertIsNone(self.ledger.head(self.sid, self.branch))
        with self.assertRaises(CheckpointConflict): self.ledger.restore_point(self.cp)
        self.ledger.data_ready(self.cp, self.data)
        self.reopen()
        self.assertIsNone(self.ledger.head(self.sid, self.branch))
        with self.assertRaises(CheckpointConflict): self.ledger.restore_point(self.cp)
        before = self.recovery.get(self.task)
        committed = self.commit()
        self.assertEqual(self.recovery.get(self.task), before)
        self.assertEqual(committed["revision"], 1)
        self.reopen()
        self.assertEqual(self.ledger.head(self.sid, self.branch), committed)
        self.assertEqual(self.ledger.restore_point(self.cp)[1]["nodeId"], self.node)

    async def test_retries_same_id_do_not_append_duplicate_commit(self):
        self.ready()
        self.commit()
        original = self.path.read_bytes()
        for _ in range(3):
            self.ledger.prepare(self.cp, self.identity, None)
            self.ledger.data_ready(self.cp, self.data)
            self.commit()
        self.assertEqual(self.path.read_bytes(), original)
        changed = copy.deepcopy(self.identity); changed["workspaceId"] = new_id("workspace")
        with self.assertRaises(CheckpointConflict): self.ledger.prepare(self.cp, changed, None)
        with self.assertRaises(CheckpointConflict): self.ledger.data_ready(self.cp, dict(self.data, snapshotId="5" * 64))

    async def test_cancel_before_commit_blocks_and_after_commit_does_not_rewind(self):
        self.ready()
        self.recovery.cancel(self.task)
        with self.assertRaises(RecoveryConflict): self.commit()
        self.assertIsNone(self.ledger.head(self.sid, self.branch))
        self.ledger.fail(self.cp, "cancelled")
        self.reopen()
        self.assertEqual(self.ledger.get(self.cp)["status"], "FAILED")

    async def test_commit_ack_after_later_cancel_is_idempotent(self):
        self.ready(); committed = self.commit()
        self.recovery.cancel(self.task)
        self.assertEqual(self.commit(), committed)
        self.assertEqual(self.ledger.head(self.sid, self.branch), committed)

    async def test_deadline_and_owner_changes_block_commit(self):
        self.ready()
        with self.assertRaises(RecoveryLimit):
            self.recovery.commit_checkpoint(self.ledger, self.cp, self.owner, self.now + timedelta(seconds=120))
        changed = copy.deepcopy(self.owner); changed["generation"] = 2
        with self.assertRaises(RecoveryConflict): self.recovery.commit_checkpoint(self.ledger, self.cp, changed, self.now)
        changed = copy.deepcopy(self.owner); changed["owner"]["podUid"] = "other-pod"
        with self.assertRaises(RecoveryConflict): self.recovery.commit_checkpoint(self.ledger, self.cp, changed, self.now)
        self.assertIsNone(self.ledger.head(self.sid, self.branch))

    async def test_head_conflict_and_revision_only_on_commit(self):
        self.ready(); first = self.commit()
        self.messages += [AIMessage(content="second diagnosis")]
        node = await self.session.append_transcript(self.task, self.messages)
        ref = await self.refs.capture(self.session, node)
        identity = dict(self.identity, sessionNodeId=node, sessionRef=ref)
        cp2 = new_id("cp")
        with self.assertRaises(CheckpointConflict): self.ledger.prepare(cp2, identity, None)
        self.ledger.prepare(cp2, identity, self.cp)
        self.ledger.data_ready(cp2, self.data)
        self.recovery.complete_boundary(self.task, self.attempt, node, [])
        second = self.recovery.commit_checkpoint(self.ledger, cp2, self.owner, self.now)
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["parentCheckpointId"], first["checkpointId"])
        self.reopen()
        self.assertEqual(self.ledger.head(self.sid, self.branch), second)

    async def test_failed_append_does_not_publish_head(self):
        self.ready()
        with patch.object(self.ledger._journal, "append", side_effect=OSError("synthetic fsync failure")):
            with self.assertRaises(OSError): self.commit()
        self.assertIsNone(self.ledger.head(self.sid, self.branch))
        self.assertEqual(self.ledger.get(self.cp)["status"], "DATA_READY")

    async def test_duplicate_boundary_and_partial_hash_rejected(self):
        self.ledger.prepare(self.cp, self.identity, None)
        with self.assertRaises(JournalCorrupt): self.ledger.prepare(new_id("cp"), self.identity, None)
        with self.assertRaises(JournalCorrupt): self.ledger.data_ready(self.cp, dict(self.data, snapshotId="12345678"))
        self.assertEqual(self.ledger.get(self.cp)["status"], "PREPARING")

    async def test_mutable_image_and_invalid_scope_rejected_before_prepare(self):
        for field,value in (("imageId","sandboxd-workspace:latest"),("imageRef","image password=synthetic"),("generation",True),("workspaceId","workspace-other")):
            identity=copy.deepcopy(self.identity);identity[field]=value
            with self.subTest(field=field):
                with self.assertRaises(JournalCorrupt): self.ledger.prepare(new_id("cp"),identity,None)
        self.assertEqual(self.ledger.pending(),[])

    async def test_prefix_publication_failure_retry_keeps_complete_reference(self):
        self.messages += [AIMessage(content="new complete boundary")]
        node = await self.session.append_transcript(self.task,self.messages)
        original = self.refs.read(self.ref)
        with patch("agentd.app.runtime.session_refs.os.replace",side_effect=OSError("synthetic publication failure")):
            with self.assertRaises(OSError): await self.refs.capture(self.session,node)
        self.assertEqual(self.refs.read(self.ref),original)
        fsync = os.fsync
        calls = 0
        def fail_directory(fd):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError("synthetic directory fsync interruption")
            return fsync(fd)
        with patch("agentd.app.runtime.session_refs.os.fsync",side_effect=fail_directory):
            with self.assertRaises(OSError): await self.refs.capture(self.session,node)
        ref=await self.refs.capture(self.session,node)
        self.assertEqual(self.refs.read(ref)["nodeId"],node)
        self.assertEqual((self.refs.directory/(ref["sha256"]+".json")).stat().st_nlink,1)

    async def test_complete_invalid_transition_refused_even_with_valid_checksum(self):
        self.ready()
        r = self.ledger.get(self.cp)
        r.update(status="COMMITTED", revision=8, committedAt=datetime.now(timezone.utc).isoformat())
        self.ledger._journal.append("checkpoint.state", r)
        with self.assertRaises(JournalCorrupt): self.reopen()

    async def test_truncated_tail_ignored_then_repaired_without_duplicate_head(self):
        self.ready(); self.commit(); before = self.path.read_bytes()
        self.ledger.close()
        with self.path.open("ab") as f: f.write(b'{"truncated":'); f.flush(); os.fsync(f.fileno())
        self.reopen()
        self.assertEqual(self.ledger.head(self.sid, self.branch)["checkpointId"], self.cp)
        # An idempotent acknowledgement does not alter the raw incomplete tail.
        self.commit()
        self.assertEqual(self.path.read_bytes(), before + b'{"truncated":')

    async def test_pending_boundary_rebuild_and_cancel_commit_lock_order(self):
        self.ready()
        self.reopen()
        self.assertEqual(self.ledger.boundary(self.task, self.attempt, self.node)["checkpointId"], self.cp)
        self.assertEqual([r["checkpointId"] for r in self.ledger.pending()], [self.cp])
        entered, release, cancel_done = threading.Event(), threading.Event(), threading.Event()
        errors = []
        original = self.ledger.commit

        def held_commit(checkpoint_id):
            entered.set()
            if not release.wait(3): raise RuntimeError("synthetic test synchronization timeout")
            return original(checkpoint_id)

        def commit():
            try: self.commit()
            except BaseException as err: errors.append(err)

        def cancel():
            try: self.recovery.cancel(self.task)
            except BaseException as err: errors.append(err)
            finally: cancel_done.set()

        with patch.object(self.ledger, "commit", side_effect=held_commit):
            writer = threading.Thread(target=commit)
            canceller = threading.Thread(target=cancel)
            writer.start()
            try:
                self.assertTrue(entered.wait(2))
                canceller.start()
                self.assertFalse(cancel_done.wait(.02))
            finally:
                release.set()
                writer.join(3)
                if canceller.ident is not None: canceller.join(3)
            self.assertFalse(writer.is_alive())
            self.assertFalse(canceller.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(self.recovery.get(self.task)["cancelRequested"])
        self.assertEqual(self.ledger.head(self.sid, self.branch)["checkpointId"], self.cp)
        self.assertEqual(self.ledger.pending(), [])

