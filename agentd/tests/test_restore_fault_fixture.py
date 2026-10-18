"""故障夹具的本地协议测试；mock信号/子进程/CRI，不当真实故障成绩。"""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentd import workspace_restore_fault_demo as demo
from agentd.workspace_process_demo import start_agent
from agentd.app.runtime.checkpoints import CheckpointLedger
from agentd.app.runtime.restore_coordinator import RestoreCoordinator


class RestoreFaultFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory(dir="/tmp", prefix="sandboxd-restore-fault-test-")
        self.addCleanup(self.tmp.cleanup);self.root = Path(self.tmp.name)
        self.request = {"restoreId": "restore-test", "taskId": "task-test", "sourceCheckpointId": "cp-test", "status": "FILES_READY"}

    async def test_native_root_and_unknown_phase_are_rejected(self):
        with self.assertRaises(ValueError):demo.native_root(self.root)
        valid = Path.home() / ".local/share/sandboxd/workspace-restore-fault" / ("a" * 32)
        self.assertEqual(demo.native_root(valid), valid.resolve())
        with patch.object(demo, "native_root", return_value=self.root):
            with self.assertRaises(ValueError):demo.install_cutpoint(self.root, "arbitrary")

    async def test_cut_before_attach_and_after_files_ready_publish_durable_marker(self):
        for phase, status, expect_before in (("before-attached", "ATTACHED", True), ("files-ready", "FILES_READY", False), ("attached", "ATTACHED", False)):
            marker = self.root / "cut-point.json"
            seen = []
            def advance(ledger, rid, state, failure=None):
                seen.append(marker.exists());return dict(self.request, status=state)
            async def called(*args, **kwargs):return {}
            ledger = SimpleNamespace(restore_request=lambda rid: dict(self.request))
            with patch.object(demo, "native_root", return_value=self.root), patch.object(CheckpointLedger, "advance_restore", advance), patch.object(RestoreCoordinator, "_call", called), patch.object(demo.os, "kill") as kill:
                demo.install_cutpoint(self.root, phase)
                record = CheckpointLedger.advance_restore(ledger, "restore-test", status)
                payload = json.loads(marker.read_text())
                self.assertEqual(payload["phase"], phase)
                self.assertEqual(payload["status"], "FILES_READY" if expect_before else status)
                self.assertEqual(seen, [expect_before])
                self.assertEqual(record["status"], status)
                kill.assert_called_once_with(os.getpid(), signal.SIGSTOP)
            marker.unlink()

    async def test_start_cut_and_wait_observation_do_not_replace_real_call(self):
        actions = []
        async def called(coordinator, action, *args, **kwargs):
            actions.append(action);return {"barrier": {"podUid": "synthetic"}}
        def advance(*args):return {}
        with patch.object(demo, "native_root", return_value=self.root), patch.object(CheckpointLedger, "advance_restore", advance), patch.object(RestoreCoordinator, "_call", called), patch.object(demo.os, "kill") as kill, patch.object(demo, "observe_maintenance", return_value={"actualRuntimeType": "synthetic"}):
            demo.install_cutpoint(self.root, "started")
            await RestoreCoordinator._call(object(), "start", self.request, {}, None)
            await RestoreCoordinator._call(object(), "wait", self.request, {}, None)
            self.assertEqual(actions, ["start", "wait"])
            kill.assert_called_once_with(os.getpid(), signal.SIGSTOP)
            self.assertTrue((self.root / "restore-maintenance.json").exists())

    async def test_cancel_gate_keeps_event_loop_responsive_and_propagates_cancel(self):
        async def called(*args, **kwargs):return {"barrier": {}}
        def advance(*args):return {}
        with patch.object(demo, "native_root", return_value=self.root), patch.object(CheckpointLedger, "advance_restore", advance), patch.object(RestoreCoordinator, "_call", called), patch.object(demo.os, "kill") as kill:
            demo.install_cutpoint(self.root, "cancel-ready")
            task = asyncio.create_task(RestoreCoordinator._call(object(), "start", self.request, {}, None))
            async with asyncio.timeout(1):
                while not (self.root / "cut-point.json").exists():await asyncio.sleep(.001)
            kill.assert_not_called()
            task.cancel()
            result = await asyncio.gather(task, return_exceptions=True)
            self.assertIsInstance(result[0], asyncio.CancelledError)

    async def test_after_tool_cut_follows_real_save_and_only_targets_restored_task(self):
        order = []
        async def save(*args, **kwargs):order.append("saved");return {"checkpointId": "cp-new"}
        async def called(*args, **kwargs):return {}
        def advance(*args):return {}
        coordinator = SimpleNamespace(ledger=SimpleNamespace(restore_requests=lambda: [dict(self.request, status="ATTACHED")]))
        with patch.object(demo, "native_root", return_value=self.root), patch.object(CheckpointLedger, "advance_restore", advance), patch.object(RestoreCoordinator, "_call", called), patch.object(demo.CheckpointCoordinator, "save", save), patch.object(demo.os, "kill") as kill:
            demo.install_cutpoint(self.root, "after-tool")
            result = await demo.CheckpointCoordinator.save(coordinator, SimpleNamespace(task_id="task-test"))
            self.assertEqual(order, ["saved"])
            self.assertEqual(result["checkpointId"], "cp-new")
            self.assertEqual(json.loads((self.root / "cut-point.json").read_text())["status"], "ATTACHED")
            kill.assert_called_once_with(os.getpid(), signal.SIGSTOP)

    async def test_child_dispatch_only_fixed_restore_modes_default_unchanged(self):
        for options in ({"restore_cutpoint": "files-ready"}, {"checkpoint_mode": "restic", "restore_cutpoint": "unknown"},
                        {"checkpoint_mode": "restic", "restore_cutpoint": "files-ready", "port": 18091},
                        {"checkpoint_mode": "restic", "restore_cutpoint": "files-ready", "checkpoint_cutpoint": "committed"}):
            with self.assertRaises(ValueError):start_agent(self.root, "synthetic-go", "synthetic-api", "synthetic-alert", 1, **options)
        with patch("agentd.workspace_process_demo.subprocess.Popen", return_value=SimpleNamespace(pid=123)) as popen:
            _, log = start_agent(self.root, "synthetic-go", "synthetic-api", "synthetic-alert", 1)
            log.close();self.assertIn("uvicorn", popen.call_args.args[0])
            _, log = start_agent(self.root, "synthetic-go", "synthetic-api", "synthetic-alert", 2,
                                 checkpoint_mode="restic", restore_cutpoint="files-ready")
            log.close();self.assertIn("agentd.workspace_restore_fault_demo", popen.call_args.args[0])
            self.assertEqual(popen.call_args.kwargs["env"]["AGENTD_ROUTER_MODE"], "off")

    async def test_phase_clock_verifier_preserves_each_original_window(self):
        claim = {"attempts": [{"attemptId": "synthetic"}], "deadline": "2026-10-18T00:03:10+00:00",
                 "timing": {"claimSeconds": 190, "loopSeconds": 120, "claimStartedAt": "2026-10-18T00:00:00+00:00", "loopStartedAt": None}}
        loop = copy.deepcopy(claim)
        loop["timing"]["loopStartedAt"] = "2026-10-18T00:00:10+00:00"
        loop["deadline"] = "2026-10-18T00:02:10+00:00"
        demo.verify_phase_clocks(claim, loop, [claim, loop])
        demo.verify_phase_clocks(loop, loop, [claim, loop])
        changed = copy.deepcopy(loop);changed["deadline"] = "2026-10-18T00:03:10+00:00"
        with self.assertRaises(AssertionError):demo.verify_phase_clocks(loop, changed, [claim, changed])
        changed = copy.deepcopy(claim);changed["deadline"] = "2026-10-18T00:03:11+00:00"
        with self.assertRaises(AssertionError):demo.verify_phase_clocks(claim, loop, [changed, loop])
