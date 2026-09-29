from __future__ import annotations
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentd.retrieval.core import QueryCase, evaluate_rankings, load_dataset, load_corpus
from agentd.retrieval.snapshot import revise

ROOT = Path(__file__).resolve().parents[1] / "retrieval/data/ops-v1"


class OpsDatasetTest(unittest.TestCase):
    def test_sources_questions_and_split_contract(self):
        chunks, cases, _ = load_dataset(ROOT / "corpus.jsonl", ROOT / "queries.jsonl")
        self.assertEqual(len({c.doc_id for c in chunks}), 40)
        self.assertEqual(len(cases), 60)
        self.assertEqual(sum(c.answerable for c in cases), 50)
        for doc in json.loads((ROOT / "manifest.json").read_text())["documents"]:
            self.assertEqual(hashlib.sha256((ROOT / doc["file"]).read_bytes()).hexdigest(), doc["sha256"])
        families = {}
        for row in map(json.loads, (ROOT / "queries.jsonl").read_text().splitlines()):
            self.assertEqual(row["reviewStatus"], "pending-human-review")
            families.setdefault(row["family"], set()).add(row["split"])
            self.assertTrue(row["requiredSteps"] and row["forbiddenConclusions"])
        self.assertTrue(all(len(splits) == 1 for splits in families.values()))

    def test_unanswerable_is_not_zero_recall(self):
        cases = [QueryCase("a", "answer", {"c": 2}), QueryCase("n", "unknown", {}, False)]
        score = evaluate_rankings(cases, {"a": ["c"], "n": ["irrelevant"]})
        self.assertEqual(score["recall@10"], 1)
        self.assertEqual(score["unanswerableCount"], 1)
        self.assertIsNone(evaluate_rankings(cases[1:], {"n": []})["mrr"])
        with self.assertRaises(ValueError):
            QueryCase.from_dict({"queryId": "x", "query": "x", "relevance": {}})

    def test_snapshot_document_replace_delete_and_original_preserved(self):
        original = (ROOT / "corpus.jsonl").read_bytes()
        with TemporaryDirectory(dir="/tmp") as directory:
            output = Path(directory) / "deleted"
            report = revise(ROOT / "corpus.jsonl", output, delete=["fixture-payments-memory"])
            self.assertNotEqual(report["parentCorpusHash"], report["corpusHash"])
            self.assertEqual(report["documentCount"], 39)
            self.assertFalse(any(c.doc_id == "fixture-payments-memory" for c in load_corpus(output / "corpus.jsonl")[0]))
            chunks, _ = load_corpus(ROOT / "corpus.jsonl")
            row = next(c.to_dict() for c in chunks if c.doc_id == "fixture-payments-memory")
            row["text"] = "Updated synthetic content."
            upsert = Path(directory) / "upsert.jsonl"
            upsert.write_text(json.dumps(row) + "\n")
            new = Path(directory) / "updated"
            revise(ROOT / "corpus.jsonl", new, upsert=upsert)
            modified = [c for c in load_corpus(new / "corpus.jsonl")[0] if c.doc_id == row["docId"]]
            self.assertEqual(len(modified), 1)
            self.assertEqual(modified[0].text, row["text"])
            with self.assertRaises(FileExistsError):
                revise(ROOT / "corpus.jsonl", new, upsert=upsert)
        self.assertEqual((ROOT / "corpus.jsonl").read_bytes(), original)
