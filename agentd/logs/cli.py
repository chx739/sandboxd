from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..retrieval.core import _percentile
from .core import LogAggregation, LogQuery, load_logs
from .fixtures import generate
from .opensearch import OpenSearchLogs
from .oracle import expected, score

ROOT = Path(__file__).resolve().parent / "data"


def run(args: argparse.Namespace) -> dict:
    if args.command == "generate":
        return generate(args.data)
    rows, digest = load_logs(args.data / "logs.jsonl")
    cases = [json.loads(line) for line in (args.data / "queries.jsonl").read_text().splitlines() if line]
    if len({c["caseId"] for c in cases}) != len(cases):
        raise ValueError("日志 caseId 重复")
    if args.command == "check":
        for case in cases:
            cls = LogAggregation if case["tool"] == "aggregate_logs" else LogQuery
            expected(rows, cls.model_validate(case["arguments"]))
        return {"kind": "offline-contract-check", "snapshotHash": digest, "recordCount": len(rows), "queryCount": len(cases)}
    with OpenSearchLogs(digest) as client:
        if args.command == "ingest":
            return client.ingest(rows)
        reports = []
        for case in cases:
            aggregate = case["tool"] == "aggregate_logs"
            query = (LogAggregation if aggregate else LogQuery).model_validate(case["arguments"])
            reference = expected(rows, query)
            start = time.monotonic()
            actual = client.search(case["arguments"], aggregate=aggregate)
            elapsed = (time.monotonic() - start) * 1000
            reports.append({**case, "expected": reference, "actual": actual,
                            "scores": score(reference, actual), "latencyMs": round(elapsed, 3)})
    latency = [r["latencyMs"] for r in reports]
    searches = [r["scores"] for r in reports if "precision" in r["scores"]]
    aggregations = [r["scores"] for r in reports if "aggregationCorrect" in r["scores"]]
    return {"kind": "real-opensearch-synthetic-logs-independent-oracle", "snapshotHash": digest,
            "recordCount": len(rows), "queryCount": len(reports),
            "passed": sum(r["scores"]["passed"] for r in reports), "p50Ms": _percentile(latency, .5),
            "p95Ms": _percentile(latency, .95),
            "searchPrecisionMacro": sum(r["precision"] for r in searches) / len(searches) if searches else None,
            "searchRecallMacro": sum(r["recall"] for r in searches) / len(searches) if searches else None,
            "aggregationCorrectRate": sum(r["aggregationCorrect"] for r in aggregations) / len(aggregations) if aggregations else None,
            "parameterSchemaAcceptanceRate": 1.0,
            "parameterMetricScope": "fixed valid structured inputs; not natural-language parameter generation",
            "cases": reports}


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenSearch 日志最小测评")
    parser.add_argument("command", choices=["generate", "check", "ingest", "eval"])
    parser.add_argument("--data", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, ensure_ascii=False, indent=2))
    if "passed" in result and result["passed"] != result["queryCount"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
