from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentd.app.config import Settings
from agentd.app.main import create_app
from agentd.app.runtime.checkpoint_coordinator import CheckpointCoordinator
from agentd.app.runtime.durable import DurableJournal


class CheckpointFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_factory_opt_in_and_lifespan_closes_control_journals(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as root:
            cfg=Settings(listen_host="127.0.0.1",listen_port=8090,api_token="synthetic-api",alert_token="synthetic-alert",
                         prometheus_url="http://unused.test",sandboxd_url="http://unused.test",sandboxd_token="synthetic-sandbox",
                         llm_mode="replay",llm_base_url="",llm_model="",llm_api_key="",llm_thinking="default",
                         replay_file=Path(__file__).resolve().parents[1]/"testdata/injection-denied.replay.json",
                         trace_dir=Path(root)/"traces",workspace_backend="nfs",recovery_dir=Path(root)/"control",checkpoint_mode="restic")
            seen={}
            def build(*args,**kwargs):seen.update(kwargs);return object()
            with patch("agentd.app.main.AgentRunner",side_effect=build):
                app=create_app(cfg)
                async with app.router.lifespan_context(app):
                    self.assertIsInstance(seen["checkpoints"],CheckpointCoordinator)
            # 能重新取得独占锁，说明生命周期关闭没有遗留控制文件占用。
            with DurableJournal(Path(root)/"control/checkpoints/checkpoints.jsonl"):pass
            with DurableJournal(Path(root)/"control/tasks.jsonl"):pass
