import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from agentd.app.policy import validate_tool_call
from agentd.logs.core import LogAggregation, LogQuery, load_logs
from agentd.logs.fixtures import generate
from agentd.logs.opensearch import OpenSearchLogs
from agentd.logs.oracle import expected, score
from agentd.retrieval.milvus_hybrid import MilvusHybrid


class OpsLogsTest(unittest.TestCase):
    def test_independent_oracle_boundary_timezone_and_incorrect_result(self):
        with TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            self.assertEqual(generate(root)["queryCount"], 40)
            rows, digest = load_logs(root / "logs.jsonl")
            query = LogQuery(start="2026-09-01T08:15:00+08:00", end="2026-09-01T00:16:00Z", level="ERROR")
            reference = expected(rows, query)
            self.assertEqual(reference["total"], 3)
            self.assertEqual(set(reference["ids"]), {"log-15-checkout-0", "log-15-payments-0", "log-15-catalog-0"})
            actual = {"total": 3, "truncated": False, "logs": [{"log_id": x} for x in reference["ids"]]}
            self.assertTrue(score(reference, actual)["passed"])
            actual["total"] = 4
            self.assertFalse(score(reference, actual)["passed"])
            aggregation = LogAggregation(start="2026-09-01T00:00:00Z", end="2026-09-01T01:00:00Z",
                                         service="payments", level="ERROR", group_by="error_code")
            self.assertEqual(expected(rows, aggregation), {"total": 15, "buckets": [{"key": "OOM_KILLED", "count": 15}]})

    def test_policy_rejects_dsl_unbounded_and_naive_time(self):
        valid = {"start": "2026-09-01T00:00:00Z", "end": "2026-09-01T01:00:00Z"}
        self.assertTrue(validate_tool_call({"name": "search_logs", "args": valid}, 0, 0)["allowed"])
        for invalid in ({**valid, "dsl": {"match_all": {}}}, {**valid, "limit": True},
                        {**valid, "end": "2026-10-01T00:00:00Z"}, {**valid, "start": "2026-09-01T00:00:00"}):
            self.assertFalse(validate_tool_call({"name": "search_logs", "args": invalid}, 0, 0)["allowed"])

    def test_connector_rejects_partial_results_and_preserves_literal_values(self):
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"timed_out": True})
        with OpenSearchLogs("a" * 64, transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(RuntimeError):
                client.search({"start": "2026-09-01T00:00:00Z", "end": "2026-09-01T01:00:00Z",
                               "service": 'x" OR *'})
            self.assertIn({"term": {"service": 'x" OR *'}}, bodies[0]["query"]["bool"]["filter"])
            with self.assertRaises(ValueError):
                client.search({"index": "other"})
        self.assertEqual(len(bodies), 1)

    def test_milvus_model_identity_and_filter_escaping(self):
        class Client:
            def search(self, **kwargs):
                self.kwargs = kwargs
                return [[]]
        client = Client()
        one = MilvusHybrid("v1", "a" * 64, embedding_id="model-a", client=client)
        two = MilvusHybrid("v1", "a" * 64, embedding_id="model-b", client=client)
        self.assertNotEqual(one.collection, two.collection)
        value = 'x" or doc_id != "'
        one.bm25.search("error", filters={"component": value})
        self.assertTrue(client.kwargs["filter"].endswith("component == " + json.dumps(value)))
        self.assertEqual(client.kwargs["anns_field"], "sparse")
        with self.assertRaises(ValueError):
            one.bm25.search("error", filters={"arbitrary_expression": "true"})
