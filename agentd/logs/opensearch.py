"""固定本机 endpoint/index；模型只能传受限参数，不能传原始 DSL。"""
from __future__ import annotations

import json
import re
from typing import Any, Sequence

import httpx

from .core import LogAggregation, LogQuery, LogRecord


class OpenSearchLogs:
    def __init__(self, digest: str, *, base_url: str = "http://127.0.0.1:9201",
                 transport: httpx.BaseTransport | None = None) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("日志快照 hash 不合法")
        if base_url not in {"http://127.0.0.1:9201", "http://localhost:9201"}:
            raise ValueError("Demo 日志仅使用固定本地 endpoint")
        self.digest = digest
        self.index = "sandboxd_logs_v1_" + digest[:16]
        self._client = httpx.Client(base_url=base_url, timeout=10, transport=transport,
                                    trust_env=False, follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OpenSearchLogs":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        with self._client.stream(method, path, **kwargs) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 2 << 20:
                    raise RuntimeError("OpenSearch 响应超过2MiB")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise RuntimeError("OpenSearch 响应格式错误")
        if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
            raise RuntimeError("OpenSearch 返回了不完整查询结果")
        return result

    def ingest(self, rows: Sequence[LogRecord]) -> dict:
        if not rows or len({r.log_id for r in rows}) != len(rows):
            raise ValueError("日志为空或 ID 重复")
        exists = self._client.head("/" + self.index)
        if exists.status_code == 404:
            self._request("PUT", "/" + self.index, json={
                "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                             "analysis": {"analyzer": {"log_text": {"type": "custom",
                                 "tokenizer": "whitespace", "filter": ["lowercase"]}}}},
                "mappings": {"dynamic": "strict", "properties": {
                    **{f: {"type": "keyword"} for f in ("log_id", "service", "level", "error_code",
                                                         "trace_id", "provenance", "snapshot_hash")},
                    "timestamp": {"type": "date"},
                    "message": {"type": "text", "analyzer": "log_text"},
                }},
            })
        else:
            exists.raise_for_status()
        for start in range(0, len(rows), 100):
            lines = []
            for row in rows[start:start + 100]:
                lines.extend([json.dumps({"index": {"_index": self.index, "_id": row.log_id}}),
                              json.dumps({**row.model_dump(), "snapshot_hash": self.digest}, ensure_ascii=False)])
            result = self._request("POST", "/_bulk", content="\n".join(lines) + "\n",
                                   headers={"Content-Type": "application/x-ndjson"})
            if result.get("errors"):
                raise RuntimeError("OpenSearch bulk 存在失败项")
        self._request("POST", f"/{self.index}/_refresh")
        count = self._request("GET", f"/{self.index}/_count")["count"]
        if count != len(rows):
            raise RuntimeError("日志快照数量不一致")
        return {"index": self.index, "snapshotHash": self.digest, "count": count}

    def query_body(self, query: LogQuery) -> dict:
        filters: list[dict] = [{"term": {"snapshot_hash": self.digest}},
                              {"range": {"timestamp": {"gte": query.start, "lt": query.end}}}]
        for field in ("service", "level", "error_code", "trace_id"):
            if value := getattr(query, field):
                filters.append({"term": {field: value}})
        if query.keyword:
            filters.append({"match_phrase": {"message": query.keyword}})
        body: dict = {"timeout": "5s", "track_total_hits": True, "query": {"bool": {"filter": filters}},
                      "size": query.limit, "sort": [{"timestamp": "asc"}, {"log_id": "asc"}]}
        if isinstance(query, LogAggregation):
            body.update(size=0)
            body.pop("sort")
            if query.group_by == "minute":
                group = {"date_histogram": {"field": "timestamp", "fixed_interval": "1m", "min_doc_count": 1}}
            else:
                group = {"terms": {"field": query.group_by, "size": 1000, "order": {"_key": "asc"}}}
            body["aggs"] = {"groups": group}
        return body

    def search(self, arguments: dict, *, aggregate: bool = False) -> dict:
        # Connector 独立校验，绕过 Agent Policy 也不能提交任意 DSL。
        query = (LogAggregation if aggregate else LogQuery).model_validate(arguments)
        result = self._request("POST", f"/{self.index}/_search", json=self.query_body(query))
        total = result["hits"]["total"]
        if not isinstance(total, dict) or total.get("relation") != "eq":
            raise RuntimeError("日志计数不是精确结果")
        common = {"snapshotHash": self.digest, "query": query.model_dump(exclude_none=True),
                  "total": total["value"], "trustLevel": "untrusted-log-evidence"}
        if aggregate:
            groups = result["aggregations"]["groups"]
            if groups.get("sum_other_doc_count", 0) or groups.get("doc_count_error_upper_bound", 0):
                raise RuntimeError("聚合被截断或含近似误差")
            return {**common, "buckets": [{"key": b["key"], "count": b["doc_count"]} for b in groups["buckets"]]}
        logs = []
        for hit in result["hits"]["hits"]:
            source = hit["_source"]
            if source.pop("snapshot_hash", None) != self.digest:
                raise RuntimeError("日志来源不匹配")
            logs.append(LogRecord.model_validate(source).model_dump())
        return {**common, "logs": logs, "truncated": total["value"] > len(logs)}
