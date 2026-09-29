"""可重建的合成故障日志与40条查询；不声称来自生产系统。"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def generate(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    def iso(minute: int, second: int = 0) -> str:
        return (base + timedelta(minutes=minute, seconds=second)).isoformat().replace("+00:00", "Z")
    rows = []
    services = ("checkout", "payments", "catalog")
    for minute in range(60):
        for service in services:
            for slot in range(4):
                fault = 15 <= minute < 30 and slot == 0
                code, message = {
                    "checkout": ("CONNECTION_REFUSED", "upstream connection refused service endpoint unavailable"),
                    "payments": ("OOM_KILLED", "container terminated memory limit exceeded restart observed"),
                    "catalog": ("READINESS_FAILED", "readiness probe failed connection timeout"),
                }[service] if fault else ("", "request completed healthy response")
                rows.append({"log_id": f"log-{minute:02}-{service}-{slot}", "timestamp": iso(minute, slot * 10),
                             "service": service, "level": "ERROR" if fault else "INFO", "error_code": code,
                             "trace_id": f"trace-{minute:02}-{slot}", "message": message, "provenance": "synthetic"})
    cases = []
    def add(kind: str, question: str, args: dict, tool: str = "search_logs") -> None:
        cases.append({"caseId": f"logs-{len(cases)+1:02}", "kind": kind, "question": question,
                      "tool": tool, "arguments": {"start": iso(0), "end": iso(60), **args},
                      "referenceSource": "independent-python-oracle", "reviewStatus": "deterministic-fixture"})
    for service in services:
        add("service", f"查询 {service} 最早5条日志", {"service": service, "limit": 5})
        add("errors", f"查询 {service} 在00:15至00:30的错误", {"service": service, "level": "ERROR", "start": iso(15), "end": iso(30)})
        add("boundary", f"查询 {service} 00:15至00:16的日志，右端点不包含", {"service": service, "start": iso(15), "end": iso(16)})
        add("empty", f"查询 {service} 00:30之后错误", {"service": service, "level": "ERROR", "start": iso(30)})
        add("count", f"统计 {service} 各级别日志数", {"service": service, "group_by": "level"}, "aggregate_logs")
        add("timeline", f"统计 {service} 的每分钟错误数", {"service": service, "level": "ERROR", "group_by": "minute"}, "aggregate_logs")
    for minute in (0, 14, 15, 29, 30, 59):
        add("trace", f"查询 trace-{minute:02}-0 关联日志", {"trace_id": f"trace-{minute:02}-0"})
    for code in ("CONNECTION_REFUSED", "OOM_KILLED", "READINESS_FAILED", "NOT_FOUND"):
        add("error_code", f"查询 {code} 日志", {"error_code": code})
    for keyword in ("memory limit", "READINESS PROBE", "connection refused", "no such evidence"):
        add("phrase", f"查询短语 {keyword}", {"keyword": keyword, "limit": 100})
    for group in ("service", "level", "error_code", "minute"):
        add("aggregate", f"统计错误日志按 {group} 分组", {"level": "ERROR", "group_by": group}, "aggregate_logs")
    add("empty", "查询不存在的服务", {"service": "missing"})
    add("timezone", "查询北京时间08:15至08:16的日志", {"start": "2026-09-01T08:15:00+08:00", "end": "2026-09-01T08:16:00+08:00"})
    add("empty_aggregate", "统计不存在服务的错误", {"service": "missing", "group_by": "level"}, "aggregate_logs")
    add("context", "查询payments在故障出现前后的日志", {"service": "payments", "start": iso(14), "end": iso(17)})
    for name, items in (("logs.jsonl", rows), ("queries.jsonl", cases)):
        (root / name).write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in items))
    return {"kind": "synthetic-log-fixture", "recordCount": len(rows), "queryCount": len(cases),
            "seed": "deterministic-no-rng-v1", "fixedNow": iso(60), "window": "[start,end)"}
