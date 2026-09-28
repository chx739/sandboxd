"""与数据库和模型无关的语料契约、RRF 与检索质量指标。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    text: str
    source: str
    corpus_version: str
    section_id: str = ""
    component: str = ""
    source_revision: str = ""

    @classmethod
    def from_dict(cls, item: Mapping[str, Any]) -> "Chunk":
        chunk = cls(
            chunk_id=str(item["chunkId"]),
            doc_id=str(item["docId"]),
            title=str(item.get("title", "")),
            text=str(item["text"]),
            source=str(item["source"]),
            corpus_version=str(item["corpusVersion"]),
            section_id=str(item.get("sectionId", "")),
            component=str(item.get("component", "")),
            source_revision=str(item.get("sourceRevision", "")),
        )
        if not _ID.fullmatch(chunk.chunk_id) or not _ID.fullmatch(chunk.doc_id):
            raise ValueError("chunkId/docId 格式不合法")
        if not _ID.fullmatch(chunk.corpus_version):
            raise ValueError("corpusVersion 格式不合法")
        if not chunk.text or len(chunk.text) > 16384:
            raise ValueError("chunk 文本为空或超过 16384 字符")
        if len(chunk.title) > 300 or len(chunk.source) > 1000 or not chunk.source:
            raise ValueError("chunk 标题或来源不合法")
        if (len(chunk.section_id) > 256 or len(chunk.component.encode()) > 128
                or len(chunk.source_revision.encode()) > 256):
            raise ValueError("chunk 元数据过长")
        return chunk

    def to_dict(self) -> dict[str, str]:
        result = {
            "chunkId": self.chunk_id,
            "docId": self.doc_id,
            "title": self.title,
            "text": self.text,
            "source": self.source,
            "corpusVersion": self.corpus_version,
        }
        # 旧语料的 hash 不因新增可选元数据而变化。
        result.update({key: value for key, value in {
            "sectionId": self.section_id, "component": self.component,
            "sourceRevision": self.source_revision,
        }.items() if value})
        return result


@dataclass(frozen=True)
class QueryCase:
    query_id: str
    query: str
    relevance: dict[str, int]

    @classmethod
    def from_dict(cls, item: Mapping[str, Any]) -> "QueryCase":
        relevance = item.get("relevance")
        if not isinstance(relevance, dict) or not relevance:
            raise ValueError("query 缺少相关标签")
        case = cls(
            query_id=str(item["queryId"]),
            query=str(item["query"]),
            relevance={str(key): int(value) for key, value in relevance.items()},
        )
        if not _ID.fullmatch(case.query_id) or not case.query.strip():
            raise ValueError("queryId/query 不合法")
        if any(not _ID.fullmatch(key) or value < 1 or value > 3 for key, value in case.relevance.items()):
            raise ValueError("相关性标签不合法")
        return case


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    score: float


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number} 不是 JSON 对象")
        items.append(value)
    return items


def load_dataset(corpus_path: Path, queries_path: Path) -> tuple[list[Chunk], list[QueryCase], str]:
    chunks = [Chunk.from_dict(item) for item in load_jsonl(corpus_path)]
    cases = [QueryCase.from_dict(item) for item in load_jsonl(queries_path)]
    if not chunks or not cases:
        raise ValueError("语料和查询不能为空")
    versions = {chunk.corpus_version for chunk in chunks}
    if len(versions) != 1:
        raise ValueError("语料混入多个版本")
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError("语料有重复 chunkId")
    if len({case.query_id for case in cases}) != len(cases):
        raise ValueError("queryId 重复")
    known = set(chunk_ids)
    for case in cases:
        if not set(case.relevance) <= known:
            raise ValueError(f"{case.query_id} 引用了未知 chunkId")
    canonical = json.dumps(
        [chunk.to_dict() for chunk in sorted(chunks, key=lambda item: item.chunk_id)],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    corpus_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return chunks, cases, corpus_hash


def rrf_fuse(
    bm25: Sequence[SearchHit],
    dense: Sequence[SearchHit],
    *,
    constant: int = 60,
    limit: int | None = None,
) -> list[SearchHit]:
    """只融合排名，不混用 BM25 与向量相似度的原始分数。"""

    if constant <= 0 or (limit is not None and limit <= 0):
        raise ValueError("RRF 参数必须为正数")
    scores: dict[str, float] = {}
    for ranking in (bm25, dense):
        seen: set[str] = set()
        for position, hit in enumerate(ranking, 1):
            if not _ID.fullmatch(hit.chunk_id):
                raise ValueError("检索结果含非法 chunkId")
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (constant + position)
    result = [SearchHit(chunk_id, score) for chunk_id, score in scores.items()]
    result.sort(key=lambda hit: (-hit.score, hit.chunk_id))
    return result[:limit] if limit is not None else result


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 2)


def evaluate_rankings(
    cases: Sequence[QueryCase],
    rankings: Mapping[str, Sequence[str]],
    latencies_ms: Mapping[str, float] | None = None,
) -> dict[str, float | int]:
    """对同一批 query 计算 Recall、MRR、nDCG@10 与 p50/p95。"""

    if not cases or set(rankings) != {case.query_id for case in cases}:
        raise ValueError("ranking 必须恰好覆盖全部 query")
    recall_1 = recall_5 = recall_10 = mrr = ndcg_10 = 0.0
    for case in cases:
        ranking = list(rankings[case.query_id])
        if len(ranking) != len(set(ranking)):
            raise ValueError("同一 query 的排名含重复 chunkId")
        relevant = set(case.relevance)
        recall_1 += len(relevant.intersection(ranking[:1])) / len(relevant)
        recall_5 += len(relevant.intersection(ranking[:5])) / len(relevant)
        recall_10 += len(relevant.intersection(ranking[:10])) / len(relevant)
        for index, chunk_id in enumerate(ranking, 1):
            if chunk_id in relevant:
                mrr += 1.0 / index
                break
        dcg = sum(
            (2 ** case.relevance.get(chunk_id, 0) - 1) / math.log2(index + 1)
            for index, chunk_id in enumerate(ranking[:10], 1)
        )
        ideal = sum(
            (2 ** grade - 1) / math.log2(index + 1)
            for index, grade in enumerate(sorted(case.relevance.values(), reverse=True)[:10], 1)
        )
        ndcg_10 += dcg / ideal
    count = len(cases)
    latencies = list((latencies_ms or {}).values())
    if latencies_ms is not None and set(latencies_ms) != set(rankings):
        raise ValueError("latency 必须恰好覆盖全部 query")
    return {
        "queryCount": count,
        "recall@1": round(recall_1 / count, 4),
        "recall@5": round(recall_5 / count, 4),
        "recall@10": round(recall_10 / count, 4),
        "mrr": round(mrr / count, 4),
        "ndcg@10": round(ndcg_10 / count, 4),
        "p50Ms": _percentile(latencies, 0.50),
        "p95Ms": _percentile(latencies, 0.95),
    }
