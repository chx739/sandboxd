"""固定安装入口的命令契约；Fake工具不能代替真实镜像验收。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]


class WorkspaceStorageBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "hack").mkdir()
        shutil.copyfile(REPO / "hack/build-workspace-storage.sh", self.root / "hack/build-workspace-storage.sh")
        self.bin = self.root / "home/.local/bin"
        self.bin.mkdir(parents=True)
        self.log = self.root / "commands.jsonl"
        self.env = {"HOME": str(self.root / "home"), "PATH": str(self.bin) + ":/usr/bin:/bin",
                    "BUILD_TEST_LOG": str(self.log), "BUILD_TEST_EXISTING": "yes"}
        for name in ("check-resources.sh", "require-demo-cluster.sh", "install-restic.sh"):
            (self.root / "hack" / name).write_text("#!/bin/bash\nexit 0\n")
        # 所有有外部副作用的命令均为此临时目录内的固定Fake。
        program = '''#!/usr/bin/python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["BUILD_TEST_LOG"], "a") as out:
    out.write(json.dumps([name, *args]) + "\\n")
if name == "go":
    if args == ["version"]:
        print("go version go1.26.5 linux/amd64")
    elif os.environ.get("BUILD_TEST_FAIL_GO") == "yes":
        sys.exit(1)
    else:
        pathlib.Path(args[args.index("-o") + 1]).write_bytes(b"synthetic-go-binary")
elif name == "docker" and args[:2] == ["image", "inspect"]:
    if "--format" not in args and os.environ["BUILD_TEST_EXISTING"] != "yes":
        sys.exit(1)
    print("sha256:" + "a" * 64)
elif name == "sha256sum" and "--status" in args:
    # 下载/摘要校验的实际正确性由真实入口复核，此处仅测命令编排。
    sys.stdin.read()
'''
        for name in ("go", "docker", "kind", "sha256sum"):
            path = self.bin / name
            path.write_text(program)
            path.chmod(0o755)
        restic = self.root / "home/.local/share/sandboxd-tools/restic-0.18.1/restic"
        restic.parent.mkdir(parents=True)
        restic.write_bytes(b"synthetic-restic")

    def run_build(self):
        return subprocess.run(["/bin/bash", "hack/build-workspace-storage.sh"], cwd=self.root,
                              env=self.env, capture_output=True, text=True, timeout=20)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_existing_tags_preserved_but_current_server_and_renderer_rebuilt(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        self.assertFalse(any(row[:2] == ["docker", "build"] for row in commands))
        builds = [row[-1] for row in commands if row[:2] == ["go", "build"]]
        self.assertEqual(builds, ["./cmd/sandboxd", "./hack/workspace-backup-pod"])
        self.assertIn(["kind", "load", "docker-image", "--name", "sandboxd",
                       "sandboxd-workspace:runtime-v1.5", "sandboxd-restic:0.18.1-v3"], commands)
        self.assertEqual((self.root / ".cache/workspace-sandboxd").read_bytes(), b"synthetic-go-binary")

    def test_first_install_builds_only_fixed_images_and_helpers(self):
        self.env["BUILD_TEST_EXISTING"] = "no"
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        builds = [row[-1] for row in commands if row[:2] == ["go", "build"]]
        self.assertEqual(builds, ["./cmd/sandbox-filetool", "./cmd/sandbox-workspace-init",
                                  "./cmd/sandbox-restic", "./cmd/sandboxd", "./hack/workspace-backup-pod"])
        images = [row[row.index("-t") + 1] for row in commands if row[:2] == ["docker", "build"]]
        self.assertEqual(images, ["sandboxd-workspace:runtime-v1.5", "sandboxd-restic:0.18.1-v3"])
        self.assertTrue(any(row[0] == "sha256sum" and "--status" in row for row in commands))

    def test_failed_build_does_not_replace_previous_server(self):
        (self.root / ".cache").mkdir()
        old = self.root / ".cache/workspace-sandboxd"
        old.write_bytes(b"previous-good-server")
        self.env["BUILD_TEST_FAIL_GO"] = "yes"
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(old.read_bytes(), b"previous-good-server")
        self.assertFalse(any(row[0] == "kind" for row in self.commands()))

    def test_cluster_gate_fails_before_install_or_build(self):
        (self.root / "hack/require-demo-cluster.sh").write_text("#!/bin/bash\nexit 1\n")
        self.assertNotEqual(self.run_build().returncode, 0)
        self.assertEqual(self.commands(), [["go", "version"]])
