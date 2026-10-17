from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentd.retrieval.core import (
    QueryCase,
    SearchHit,
    evaluate_rankings,
    load_dataset,
    rrf_fuse,
)


class RetrievalCoreTest(unittest.TestCase):
    def test_versioned_runbook_and_hash_do_not_depend_on_line_order(self) -> None:
        data = Path(__file__).resolve().parents[1] / "retrieval/data"
        corpus = data / "runbook-v1.corpus.jsonl"
        queries = data / "runbook-v1.queries.jsonl"
        chunks, cases, digest = load_dataset(corpus, queries)
        self.assertEqual(len(chunks), 9)
        self.assertEqual(len(cases), 7)
        self.assertEqual({chunk.corpus_version for chunk in chunks}, {"runbook-v1"})
        with TemporaryDirectory(dir="/tmp") as directory:
            reordered = Path(directory) / "corpus.jsonl"
            reordered.write_text("\n".join(reversed(corpus.read_text().splitlines())) + "\n")
            self.assertEqual(load_dataset(reordered, queries)[2], digest)

    def test_rrf_uses_rank_not_raw_score_and_stable_ties(self) -> None:
        bm25 = [SearchHit("a", 1000), SearchHit("b", 1)]
        dense = [SearchHit("b", 0.9), SearchHit("c", 0.8)]
        result = rrf_fuse(bm25, dense)
        self.assertEqual([hit.chunk_id for hit in result], ["b", "a", "c"])
        self.assertAlmostEqual(result[0].score, 1 / 62 + 1 / 61)
        self.assertEqual([hit.chunk_id for hit in rrf_fuse(bm25, dense, limit=2)], ["b", "a"])
        with self.assertRaises(ValueError):
            rrf_fuse(bm25, dense, constant=0)

    def test_recall_mrr_ndcg_and_latency(self) -> None:
        cases = [
            QueryCase("q1", "one", {"a": 2}),
            QueryCase("q2", "two", {"b": 1, "c": 2}),
        ]
        metrics = evaluate_rankings(
            cases,
            {"q1": ["x", "a"], "q2": ["c", "b"]},
            {"q1": 10.0, "q2": 20.0},
        )
        self.assertEqual(metrics["recall@1"], 0.25)
        self.assertEqual(metrics["recall@5"], 1.0)
        self.assertEqual(metrics["mrr"], 0.75)
        self.assertLess(metrics["ndcg@10"], 1.0)
        self.assertEqual(metrics["p50Ms"], 10.0)
        self.assertEqual(metrics["p95Ms"], 20.0)
        with self.assertRaises(ValueError):
            evaluate_rankings(cases, {"q1": ["a"]})

    def test_rejects_unknown_qrel_and_duplicate_chunk(self) -> None:
        with TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            corpus = root / "corpus.jsonl"
            queries = root / "queries.jsonl"
            chunk = {
                "chunkId": "a", "docId": "a", "title": "A", "text": "alpha",
                "source": "synthetic://a", "corpusVersion": "v1",
            }
            corpus.write_text(json.dumps(chunk) + "\n")
            queries.write_text(json.dumps({
                "queryId": "q1", "query": "alpha", "relevance": {"missing": 1},
            }) + "\n")
            with self.assertRaises(ValueError):
                load_dataset(corpus, queries)
            queries.write_text(json.dumps({
                "queryId": "q1", "query": "alpha", "relevance": {"a": 1},
            }) + "\n")
            corpus.write_text(json.dumps(chunk) + "\n" + json.dumps(chunk) + "\n")
            with self.assertRaises(ValueError):
                load_dataset(corpus, queries)


if __name__ == "__main__":
    unittest.main()
