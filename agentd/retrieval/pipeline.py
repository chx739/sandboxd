"""BM25 + dense → RRF → BGE；默认同一 Milvus 快照，保留旧后端对照。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence

from ..app.redaction import public_error
from .core import Chunk, rrf_fuse


@dataclass(frozen=True)
class QueryRun:
    rankings: dict[str, list[str]]
    latencies_ms: dict[str, float]
    evidence: list[dict[str, Any]]


class HybridRetriever:
    def __init__(self, chunks: Sequence[Chunk], es: Any, milvus: Any, bge: Any) -> None:
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        if len(self._chunks) != len(chunks):
            raise ValueError("重复 chunkId")
        self._es = es
        self._milvus = milvus
        self._bge = bge

    def query(self, text: str, *, recall_limit: int = 20, rerank_limit: int = 20, output_limit: int = 10,
              filters: dict[str, str] | None = None) -> QueryRun:
        if not text.strip() or not 1 <= output_limit <= rerank_limit <= 100 or not 1 <= recall_limit <= 100:
            raise ValueError("检索参数不合法")
        started = time.monotonic()
        kwargs = {"filters": filters} if filters else {}
        bm25 = self._es.search(text, recall_limit, **kwargs)
        after_bm25 = time.monotonic()
        vector = self._bge.encode_query(text)
        dense = self._milvus.search(vector, recall_limit, **kwargs)
        after_dense = time.monotonic()
        fused = rrf_fuse(bm25, dense, limit=rerank_limit)
        after_rrf = time.monotonic()
        for hit in [*bm25, *dense]:
            if hit.chunk_id not in self._chunks:
                raise RuntimeError("索引返回了本地语料以外的 chunkId")
        rerank_scores = self._bge.rerank(
            text,
            [self._chunks[hit.chunk_id].title + "\n" + self._chunks[hit.chunk_id].text for hit in fused],
        )
        if len(rerank_scores) != len(fused):
            raise RuntimeError("BGE 重排分数数量不匹配")
        reranked = sorted(
            zip(fused, rerank_scores, strict=True),
            key=lambda pair: (-pair[1], -pair[0].score, pair[0].chunk_id),
        )
        after_rerank = time.monotonic()
        evidence = []
        for hit, score in reranked[:output_limit]:
            chunk = self._chunks[hit.chunk_id]
            evidence.append({
                "chunkId": chunk.chunk_id,
                "docId": chunk.doc_id,
                "title": chunk.title,
                "snippet": public_error(chunk.text, limit=1200),
                "source": chunk.source,
                "corpusVersion": chunk.corpus_version,
                "sectionId": chunk.section_id,
                "component": chunk.component,
                "sourceRevision": chunk.source_revision,
                "rerankerScore": round(float(score), 5),
                "trustLevel": "untrusted-retrieved-evidence",
            })
        return QueryRun(
            rankings={
                "bm25": [hit.chunk_id for hit in bm25],
                "dense": [hit.chunk_id for hit in dense],
                "rrf": [hit.chunk_id for hit in fused],
                "rerank": [hit.chunk_id for hit, _ in reranked],
            },
            latencies_ms={
                "bm25": (after_bm25 - started) * 1000,
                "dense": (after_dense - after_bm25) * 1000,
                "rrf": (after_rrf - started) * 1000,
                "rerank": (after_rerank - started) * 1000,
                "rerankerOnly": (after_rerank - after_rrf) * 1000,
            },
            evidence=evidence,
        )
