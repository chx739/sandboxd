"""只校验管理夹具协议；不冒充真实进程/存储验收。"""
import json
from pathlib import Path
import signal
import socket
import tempfile
import unittest
from unittest.mock import patch

from agentd import workspace_checkpoint_fault_demo as fixture
from agentd.workspace_process_demo import start_agent


class CheckpointFaultFixtureTests(unittest.TestCase):
    def test_fixed_cut_points_pause_before_or_after_selected_transition(self):
        for phase in ("preparing","before-data-ready","data-ready","before-commit","committed"):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory(dir="/tmp") as directory:
                root=Path(directory)
                class Ledger:
                    status="DATA_READY" if phase=="before-commit" else "PREPARING" if phase=="before-data-ready" else "NEW"
                    def record(self):
                        return {"checkpointId":"cp-fixture","status":self.status,
                                "identity":{"sessionId":"session-fixture","branchId":"branch-fixture"}}
                    def get(self,cp):return self.record()
                    def head(self,*args):return self.record() if self.status=="COMMITTED" else None
                    def prepare(self,*args):self.status="PREPARING";return self.record()
                    def data_ready(self,*args):self.status="DATA_READY";return self.record()
                    def commit(self,*args):self.status="COMMITTED";return self.record()
                with patch.object(fixture,"CheckpointLedger",Ledger),patch.object(fixture,"native_root",return_value=root),patch.object(fixture.os,"kill") as stop:
                    fixture.install_cutpoint(root,phase)
                    ledger=Ledger()
                    getattr(ledger,{"preparing":"prepare","before-data-ready":"data_ready","data-ready":"data_ready"}.get(phase,"commit"))("cp-fixture")
                    marker=json.loads((root/"cut-point.json").read_text())
                    self.assertEqual(marker["status"],{"preparing":"PREPARING","before-data-ready":"PREPARING","data-ready":"DATA_READY","before-commit":"DATA_READY","committed":"COMMITTED"}[phase])
                    self.assertEqual(marker["headCheckpointId"],"cp-fixture" if phase=="committed" else None)
                    self.assertEqual((root/"cut-point.json").stat().st_mode&0o777,0o600)
                    stop.assert_called_once_with(fixture.os.getpid(),signal.SIGSTOP)

    def test_reserved_cut_happens_after_go_freeze_before_python_prepare(self):
        import asyncio
        class Ledger:
            def head(self,*args):return None
        class Coordinator:
            ledger=Ledger()
            async def _call(self,*args,**kwargs):return {"barrier":{"phase":"reserved"}}
        class Recovery:
            def snapshot(self):return {"sessionId":"session-fixture","branchId":"branch-fixture"}
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root=Path(directory)
            with patch.object(fixture,"CheckpointCoordinator",Coordinator),patch.object(fixture,"native_root",return_value=root),patch.object(fixture.os,"kill") as stop:
                fixture.install_cutpoint(root,"reserved")
                result=asyncio.run(Coordinator()._call("reserve",{},"cp-fixture","job-fixture",Recovery()))
                self.assertEqual(result["barrier"]["phase"],"reserved")
                self.assertEqual(json.loads((root/"cut-point.json").read_text())["status"],"RESERVED")
                stop.assert_called_once()

    def test_launch_defaults_preserved_and_child_mode_explicit(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root=Path(directory)
            with patch("agentd.workspace_process_demo.subprocess.Popen") as process:
                process.return_value.pid=12345
                child,log=start_agent(root,"synthetic-go","synthetic-api","synthetic-alert",1)
                log.close()
                args,kwargs=process.call_args
                self.assertIn("uvicorn",args[0])
                self.assertEqual(kwargs["env"]["AGENTD_CHECKPOINT_MODE"],"off")
                child,log=start_agent(root,"synthetic-go","synthetic-api","synthetic-alert",2,checkpoint_mode="restic",checkpoint_cutpoint="committed")
                log.close()
                args,kwargs=process.call_args
                self.assertIn("agentd.workspace_checkpoint_fault_demo",args[0])
                self.assertEqual(kwargs["env"]["AGENTD_CHECKPOINT_MODE"],"restic")
                self.assertNotIn("SANDBOXD_OPERATOR_TOKEN",kwargs["env"])
            with self.assertRaises(ValueError):
                start_agent(root,"synthetic-go","synthetic-api","synthetic-alert",3,checkpoint_cutpoint="committed")

    def test_cut_point_root_is_private_fixture_scope(self):
        with self.assertRaises(ValueError):fixture.native_root(Path("/tmp/arbitrary"))

    def test_port_probe_still_rejects_a_live_listener(self):
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            server.bind(("127.0.0.1",0));server.listen(1)
            port=server.getsockname()[1]
            with self.assertRaises(OSError):fixture.require_idle_ports((port,))
        fixture.require_idle_ports((port,))
