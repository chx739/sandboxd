from __future__ import annotations

import asyncio
import random
import unittest
from pathlib import Path
from unittest.mock import patch

from agentd.workspace_benchmark import SCENARIOS, dataset, percentile, summary, run, nfs_resources


class WorkspaceBenchmarkFixtureTests(unittest.TestCase):
    def test_fixed_dataset_is_reproducible_and_within_file_boundaries(self):
        before = random.getstate()
        for name, (count, size) in SCENARIOS.items():
            with self.subTest(name=name):
                files = dataset(name)
                self.assertEqual(files, dataset(name))
                self.assertEqual(len(files), count)
                self.assertLessEqual(count, 100)
                self.assertTrue(all(len(content.encode()) == size and content.endswith("\n") for content in files.values()))
                self.assertTrue(all(path.startswith("reports/") and ".." not in path for path in files))
                self.assertLessEqual(sum(len(v.encode()) for v in files.values()), 16 << 20)
        self.assertEqual(random.getstate(), before)

    def test_summary_quantile_is_explicit_and_empty_has_no_invented_score(self):
        self.assertAlmostEqual(percentile([5, 1, 4, 2, 3], .95), 4.8)
        self.assertEqual(summary([]), {"samples": 0})
        self.assertEqual(summary([9])["p95Ms"], 9)
        self.assertEqual(summary([1, 2, 3])["p50Ms"], 2)

    def test_unknown_dataset_and_unbounded_repeats_fail_before_services(self):
        with self.assertRaises(ValueError):
            dataset("unbounded")
        for repeats in (0, 6):
            with self.assertRaises(ValueError):
                asyncio.run(run(Path("/tmp/unused.json"), Path("/tmp/unused-binary"), "127.0.0.1", repeats))
        with self.assertRaises(ValueError):
            asyncio.run(run(Path("/tmp/unused.json"), Path("/tmp/unused-binary"), "127.0.0.1", 1, "unverified"))

    def test_resource_units_and_scope_are_explicit(self):
        with patch("agentd.workspace_benchmark.checked", side_effect=["usage_usec 2500000\nuser_usec 1000000\n", "4096\n"]):
            counters = nfs_resources()
        self.assertEqual(counters["cpuSeconds"], 2.5)
        self.assertEqual(counters["memoryBytes"], 4096)
        self.assertIn("not peak", counters["scope"])
