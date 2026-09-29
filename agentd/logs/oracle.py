"""只遍历固定日志的独立判分实现；不读取生成的 OpenSearch DSL。"""
from collections import Counter
from typing import Sequence

from .core import LogAggregation, LogQuery, LogRecord, timestamp


def expected(rows: Sequence[LogRecord], query: LogQuery) -> dict:
    selected = []
    for row in rows:
        if not timestamp(query.start) <= timestamp(row.timestamp) < timestamp(query.end):
            continue
        if any(getattr(query, name) is not None and getattr(row, name) != getattr(query, name)
               for name in ("service", "level", "error_code", "trace_id")):
            continue
        if query.keyword:
            # 索引采用 whitespace + lowercase，短语必须是连续 token。
            needle, tokens = query.keyword.lower().split(), row.message.lower().split()
            if not any(tokens[i:i + len(needle)] == needle for i in range(len(tokens) - len(needle) + 1)):
                continue
        selected.append(row)
    if isinstance(query, LogAggregation):
        counts = Counter(
            int(timestamp(r.timestamp).replace(second=0, microsecond=0).timestamp() * 1000)
            if query.group_by == "minute" else getattr(r, query.group_by) for r in selected
        )
        return {"total": len(selected), "buckets": [{"key": k, "count": counts[k]} for k in sorted(counts)]}
    selected.sort(key=lambda r: (timestamp(r.timestamp), r.log_id))
    return {"total": len(selected), "ids": [r.log_id for r in selected[:query.limit]],
            "truncated": len(selected) > query.limit}


def score(expected_result: dict, actual: dict) -> dict:
    total_ok = actual.get("total") == expected_result["total"]
    if "buckets" in expected_result:
        ok = actual.get("buckets") == expected_result["buckets"]
        return {"passed": total_ok and ok, "totalCorrect": total_ok, "aggregationCorrect": ok}
    expected_ids = expected_result["ids"]
    actual_ids = [r["log_id"] for r in actual.get("logs", [])]
    relevant, found = set(expected_ids), set(actual_ids)
    precision = len(relevant & found) / len(found) if found else float(not relevant)
    recall = len(relevant & found) / len(relevant) if relevant else float(not found)
    ordered_ok = expected_ids == actual_ids
    truncation_ok = actual.get("truncated") == expected_result["truncated"]
    return {"passed": total_ok and ordered_ok and truncation_ok,
            "totalCorrect": total_ok, "orderedIdsCorrect": ordered_ok,
            "truncationCorrect": truncation_ok, "precision": precision, "recall": recall}
