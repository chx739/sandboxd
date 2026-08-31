from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentd.app.tools.files import FileWorkspace, MAX_FILE_BYTES


class FileWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        # 权限断言必须落在 WSL 原生文件系统，不能被 Windows TEMP/DrvFS 语义干扰。
        self.temporary = TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.workspace = FileWorkspace(self.root, "task-0123456789abcdef")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_write_read_search_edit_and_cas(self) -> None:
        created = self.workspace.write_file(
            "notes/diagnosis.md",
            "root cause: exit 1\napi_key=top-secret-token\n",
        )
        target = self.workspace.root / "notes" / "diagnosis.md"
        self.assertTrue(created["created"])
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("top-secret-token", created["diff"])
        self.assertTrue(created["redacted"])

        read = self.workspace.read_file("notes/diagnosis.md")
        self.assertIn("root cause", read["content"])
        self.assertNotIn("top-secret-token", read["content"])
        self.assertTrue(read["redacted"])

        found = self.workspace.search_files("root cause")
        self.assertEqual(found["matches"][0]["line"], 1)
        listed = self.workspace.list_files()
        self.assertEqual(listed["entries"][0]["path"], "notes/diagnosis.md")

        with self.assertRaisesRegex(ValueError, "expectedSha256"):
            self.workspace.write_file("notes/diagnosis.md", "overwrite")
        with self.assertRaisesRegex(ValueError, "不匹配"):
            self.workspace.write_file(
                "notes/diagnosis.md",
                "overwrite",
                "0" * 64,
            )

        edited = self.workspace.edit_file(
            "notes/diagnosis.md",
            "exit 1",
            "configuration error",
            read["sha256"],
        )
        self.assertNotEqual(edited["oldSha256"], edited["newSha256"])
        self.assertIn(
            "configuration error",
            self.workspace.read_file("notes/diagnosis.md")["content"],
        )

    def test_rejects_escape_symlink_duplicate_edit_and_large_file(self) -> None:
        for path in ("../escape", "/etc/passwd", "a//b", "a/./b", "a\\b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.workspace.write_file(path, "x")

        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        os.symlink(outside, self.workspace.root / "link.txt")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            self.workspace.read_file("link.txt")

        created = self.workspace.write_file("duplicate.txt", "same same")
        with self.assertRaisesRegex(ValueError, "恰好出现一次"):
            self.workspace.edit_file(
                "duplicate.txt",
                "same",
                "new",
                created["newSha256"],
            )
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            self.workspace.write_file("large.txt", "x" * (MAX_FILE_BYTES + 1))

    def test_each_task_has_an_independent_root(self) -> None:
        self.workspace.write_file("private.txt", "task one")
        other = FileWorkspace(self.root, "task-fedcba9876543210")
        with self.assertRaisesRegex(ValueError, "不存在"):
            other.read_file("private.txt")


if __name__ == "__main__":
    unittest.main()
