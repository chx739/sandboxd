"""Elasticsearch 8.x 原生 text 倒排/BM25；通过现有 httpx 调用 REST。"""

from __future__ import annotations

import json
import re
from typing import Sequence

import httpx

from .core import Chunk, SearchHit

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def index_name(version: str, corpus_hash: str) -> str:
    if not _VERSION.fullmatch(version) or not re.fullmatch(r"[a-f0-9]{64}", corpus_hash):
        raise ValueError("语料版本或 hash 不合法")
    return f"sandboxd_rag_{version.lower()}_{corpus_hash[:12]}"


class ElasticsearchBM25:
    def __init__(
        self,
        version: str,
        corpus_hash: str,
        *,
        base_url: str = "http://127.0.0.1:9200",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if base_url != "http://127.0.0.1:9200" and not base_url.startswith("http://localhost:"):
            raise ValueError("Demo ES 仅连接本机 HTTP 服务")
        self.index = index_name(version, corpus_hash)
        self.version = version
        self.corpus_hash = corpus_hash
        self._client = httpx.Client(base_url=base_url, timeout=30, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ElasticsearchBM25":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def rebuild(self, chunks: Sequence[Chunk]) -> dict[str, object]:
        if not chunks or any(chunk.corpus_version != self.version for chunk in chunks):
            raise ValueError("语料为空或版本不一致")
        # 只删除由版本+语料 hash 命名的本项目索引，不触及其他索引。
        exists = self._client.head(f"/{self.index}")
        if exists.status_code == 200:
            self._check(self._client.delete(f"/{self.index}"))
        elif exists.status_code != 404:
            self._check(exists)
        self._check(self._client.put(f"/{self.index}", json={
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {"properties": {
                "chunkId": {"type": "keyword"},
                "docId": {"type": "keyword"},
                "corpusVersion": {"type": "keyword"},
                "corpusHash": {"type": "keyword"},
                "source": {"type": "keyword", "ignore_above": 1000},
                "title": {"type": "text", "similarity": "BM25"},
                "text": {"type": "text", "similarity": "BM25"},
            }},
        }))
        for start in range(0, len(chunks), 100):
            lines = []
            for chunk in chunks[start:start + 100]:
                lines.append(json.dumps({"index": {"_index": self.index, "_id": chunk.chunk_id}}))
                lines.append(json.dumps({
                    **chunk.to_dict(), "corpusHash": self.corpus_hash,
                }, ensure_ascii=False))
            response = self._check(self._client.post(
                "/_bulk", content="\n".join(lines) + "\n",
                headers={"Content-Type": "application/x-ndjson"},
            ))
            if response.get("errors"):
                raise RuntimeError("ES bulk 索引存在单条失败")
        self._check(self._client.post(f"/{self.index}/_refresh"))
        count = self.count()
        if count != len(chunks):
            raise RuntimeError(f"ES count={count}，期望 {len(chunks)}")
        return {"index": self.index, "chunkCount": count, "corpusHash": self.corpus_hash}

    def count(self) -> int:
        return int(self._check(self._client.get(f"/{self.index}/_count"))["count"])

    def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError("query 为空或 limit 超出范围")
        response = self._check(self._client.post(f"/{self.index}/_search", json={
            "size": limit,
            "_source": ["chunkId", "corpusHash"],
            "query": {"bool": {
                "must": [{"multi_match": {"query": query, "fields": ["title^2", "text"]}}],
                "filter": [{"term": {"corpusHash": self.corpus_hash}}],
            }},
        }))
        hits = response.get("hits", {}).get("hits", [])
        if not isinstance(hits, list):
            raise RuntimeError("ES search 响应格式错误")
        result = []
        for item in hits:
            source = item.get("_source", {})
            if source.get("corpusHash") != self.corpus_hash:
                raise RuntimeError("ES 返回了不同语料版本")
            result.append(SearchHit(str(source["chunkId"]), float(item.get("_score") or 0.0)))
        return result

    @staticmethod
    def _check(response: httpx.Response) -> dict:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("ES 响应不是 JSON 对象")
        return payload
