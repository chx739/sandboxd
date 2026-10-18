"""固定微基准的边界与单位；不启动真实服务。"""
import asyncio
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agentd.workspace_checkpoint_benchmark import CONTAINERS, delta, run


class CheckpointBenchmarkTests(unittest.TestCase):
    def test_counter_deltas_preserve_endpoints_and_units(self):
        before = {"go": {"cpuSeconds": 1, "rssKiB": 100}, "controller": {"cpuSeconds": 2, "rssKiB": 200},
                  "containers": {c: {"rx_bytes": 10, "tx_bytes": 20, "cpuSeconds": 3, "memoryBytes": 4096} for c in CONTAINERS}}
        after = copy.deepcopy(before); after["go"]["cpuSeconds"] = 1.5; after["controller"]["cpuSeconds"] = 2.25
        for value in after["containers"].values(): value.update(rx_bytes=110, tx_bytes=220, cpuSeconds=4, memoryBytes=8192)
        value = delta(before, after)
        self.assertEqual(value["before"], before); self.assertEqual(value["after"], after)
        self.assertEqual(value["delta"]["goCpuSeconds"], .5)
        self.assertEqual(value["delta"]["controllerCpuSeconds"], .25)
        for container in CONTAINERS:
            self.assertEqual(value["delta"]["containers"][container], {"rx_bytes": 100, "tx_bytes": 200, "cpuSeconds": 1})

    def test_counter_reset_is_not_silently_reported_as_zero(self):
        before = {"go": {"cpuSeconds": 1}, "controller": {"cpuSeconds": 2},
                  "containers": {c: {"rx_bytes": 10, "tx_bytes": 20, "cpuSeconds": 3, "memoryBytes": 4096} for c in CONTAINERS}}
        after = copy.deepcopy(before); after["containers"][CONTAINERS[0]]["rx_bytes"] = 0
        with self.assertRaises(AssertionError): delta(before, after)

    def test_invalid_repeats_and_existing_evidence_fail_before_services(self):
        with TemporaryDirectory(dir="/tmp") as directory, patch("agentd.workspace_checkpoint_benchmark.require_idle_ports", side_effect=AssertionError("must not access services")):
            output = Path(directory) / "report.json"
            for repeats in (0, 6):
                with self.assertRaises(ValueError): asyncio.run(run(output, repeats))
            output.write_text("preserve")
            with self.assertRaises(ValueError): asyncio.run(run(output, 1))
            self.assertEqual(output.read_text(), "preserve")
