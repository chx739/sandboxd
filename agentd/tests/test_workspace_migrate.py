from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from langchain_core.messages import HumanMessage, AIMessage

from agentd.app.clients import HTTPResult
from agentd.app.models import AlertEvent
from agentd.app.runtime.durable import JournalInUse, JournalCorrupt
from agentd.app.runtime.session import SessionJournal
from agentd.app.runtime.workspace_imports import WorkspaceImports
from agentd.app.store import TaskStore, TaskConflictError
from agentd.tests.test_recovery_runner import FakeWorkspaceService
from agentd.workspace_migrate import plan, migrate, source_files


class ImportService(FakeWorkspaceService):
    """明确Fake；真实Go/gVisor/NFS另由管理夹具验收。"""
    async def get_workspace(self, workspace):
        return HTTPResult(404, {}) if self.record is None else self.result()

    async def workspace_files(self, binding, operation):
        if operation["operation"] == "list_files":
            return HTTPResult(200, {"path": ".", "truncated": False, "entries": [
                {"path": p, "bytes": len(v.encode()), "sha256": hashlib.sha256(v.encode()).hexdigest()}
                for p, v in sorted(self.files.items())]})
        response = await super().workspace_files(binding, operation)
        if operation["operation"] == "write_file":
            response.body["newSha256"] = response.body["sha256"]
        return response


class WorkspaceMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sandboxd-migration-", dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.control = self.root / "control"
        self.source = self.root / "old-files/task-0123456789abcdef"
        self.source.mkdir(parents=True)
        (self.source / "report.md").write_text("synthetic prior investigation\n")
        self.session_id = "session-0123456789abcdef"
        self.task_id = "task-0123456789abcdef"
        journal = SessionJournal(self.root / "old-sessions", self.session_id)
        await journal.initialize(self.task_id, AlertEvent(annotations={"summary": "synthetic old task"}))
        await journal.append_transcript(self.task_id, [HumanMessage(content="inspect"), AIMessage(content="old findings")])
        await journal.append_result(self.task_id, "succeeded", "done")
        self.session = journal.path
        self.original = self.session.read_bytes()
        self.client = ImportService()

    async def preview(self):
        return (await plan(self.source, self.session, self.session_id, self.task_id))[0]

    async def apply(self, info=None):
        info = info or await self.preview()
        return await migrate(control=self.control, source=self.source, session_path=self.session,
                             session_id=self.session_id, task_id=self.task_id, expected_manifest=info["manifestSha256"],
                             expected_session=info["sourceSessionSha256"], client=self.client)

    async def test_preview_has_no_control_or_source_mutation(self):
        info = await self.preview()
        self.assertEqual(info["files"][0]["path"], "report.md")
        self.assertFalse(self.control.exists())
        self.assertEqual(self.session.read_bytes(), self.original)
        self.assertEqual(self.client.claims, 0)

    async def test_import_is_idempotent_and_resume_uses_same_workspace(self):
        imported = await self.apply()
        self.assertEqual(imported["status"], "COMMITTED")
        self.assertEqual(self.client.writes[0][1], "report.md")
        original_journal = (self.control / "imports.jsonl").read_bytes()
        self.assertEqual(await self.apply(), imported)
        self.assertEqual((self.control / "imports.jsonl").read_bytes(), original_journal)
        store = TaskStore(self.root / "old-traces", recovery_dir=self.control)
        self.addCleanup(store.close)
        task = await store.resume(self.session_id)
        self.assertEqual(task.workspace_id, imported["workspaceId"])
        self.assertEqual(task.branch_id, imported["branchId"])
        self.assertNotEqual(task.task_id, self.task_id)
        self.assertEqual(self.session.read_bytes(), self.original)
        with self.assertRaises(TaskConflictError):
            await store.resume(self.session_id)
        with self.assertRaises(JournalInUse):
            await self.apply()

    async def test_unknown_write_retry_verifies_hash_and_does_not_write_twice(self):
        self.client.lose_write = True
        with self.assertRaises(Exception):
            await self.apply()
        self.assertTrue(self.client.record["owner"]["stopConfirmed"])
        imported = await self.apply()
        self.assertEqual(imported["status"], "COMMITTED")
        self.assertEqual(len(imported["attempts"]), 2)
        self.assertEqual(len(self.client.writes), 1)

    async def test_changed_preview_and_foreign_target_fail_without_mapping(self):
        info = await self.preview()
        (self.source / "report.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "preview"):
            await self.apply(info)
        self.assertEqual(self.client.claims, 0)
        self.client.files["unexpected.md"] = "do not overwrite"
        with self.assertRaisesRegex(ValueError, "unexpected"):
            await self.apply()
        self.assertEqual(self.client.writes, [])
        with_workspace = WorkspaceImports(self.control / "imports.jsonl")
        self.addCleanup(with_workspace.close)
        self.assertEqual(with_workspace.get(self.session_id)["status"], "PREPARING")

    async def test_legacy_linear_copy_migrates_without_touching_source(self):
        entries = [json.loads(line) for line in self.original.splitlines()]
        self.session.write_text("".join(json.dumps(row) + "\n" for row in entries if row["type"] not in {"session.node", "session.head"}))
        original = self.session.read_bytes()
        imported = await self.apply()
        copied = self.control / "sessions" / (self.session_id + ".jsonl")
        self.assertTrue(copied.read_bytes().startswith(original))
        self.assertGreater(len(copied.read_bytes()), len(original))
        self.assertEqual(self.session.read_bytes(), original)
        self.assertEqual(await self.apply(), imported)

    async def test_symlink_hardlink_fifo_empty_dir_and_binary_are_rejected(self):
        path = self.source / "invalid"
        import os
        for kind in ("symlink", "hardlink", "fifo", "empty-dir", "binary"):
            with self.subTest(kind=kind):
                if kind == "symlink": path.symlink_to(self.source / "report.md")
                elif kind == "hardlink": os.link(self.source / "report.md", path)
                elif kind == "fifo": os.mkfifo(path)
                elif kind == "empty-dir": path.mkdir()
                else: path.write_bytes(b"\xff")
                try:
                    with self.assertRaises((ValueError, OSError)):
                        source_files(self.source)
                finally:
                    path.rmdir() if path.is_dir() else path.unlink()

    async def test_committed_import_cannot_be_rebound_or_mutated(self):
        imported = await self.apply()
        registry = WorkspaceImports(self.control / "imports.jsonl")
        self.addCleanup(registry.close)
        before = registry.journal.path.read_bytes()
        imported["workspaceId"] = "workspace-" + "a" * 32
        with self.assertRaises(JournalCorrupt):
            registry.save(imported)
        self.assertEqual(registry.journal.path.read_bytes(), before)
