"""Milvus 2.5 同库 BM25/Dense；内容快照重建不覆盖旧索引。"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from .core import Chunk, SearchHit
from .milvus_dense import collection_name

FILTER_FIELDS = {"component", "source", "source_revision", "doc_id"}


class MilvusBM25:
    """仅提供 lexical search 视图；Client 生命周期由 MilvusHybrid 管理。"""

    def __init__(self, index: "MilvusHybrid") -> None:
        self.index = index

    def search(self, text: str, limit: int = 20, *, filters: Mapping[str, str] | None = None) -> list[SearchHit]:
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise ValueError("BM25 query 必须为 1..4096 字符")
        return self.index._search(text, "sparse", "BM25", limit, filters)


class MilvusHybrid:
    def __init__(self, version: str, corpus_hash: str, *, embedding_id: str,
                 uri: str = "http://127.0.0.1:19530", client: Any | None = None) -> None:
        if uri not in {"http://127.0.0.1:19530", "http://localhost:19530"}:
            raise ValueError("Demo Milvus 仅连接本机服务")
        if not embedding_id.strip():
            raise ValueError("必须提供固定 embedding 标识")
        # 同一语料换模型也要不同索引；避免维度相同但语义空间不同造成静默错误。
        config = hashlib.sha256((embedding_id + ":standard:hybrid-v1").encode()).hexdigest()[:8]
        self.collection = collection_name(version, corpus_hash) + "_h1_" + config
        self.version, self.corpus_hash = version, corpus_hash
        self.embedding_id = embedding_id
        if client is None:
            from pymilvus import MilvusClient
            client = MilvusClient(uri=uri, timeout=30)
        self._client = client
        self.bm25 = MilvusBM25(self)

    def __enter__(self) -> "MilvusHybrid":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _filter(self, filters: Mapping[str, str] | None) -> str:
        conditions = ["corpus_hash == " + json.dumps(self.corpus_hash)]
        for key, value in (filters or {}).items():
            if key not in FILTER_FIELDS or not isinstance(value, str) or not value or len(value) > 1000:
                raise ValueError("检索 filter 字段或值不合法")
            conditions.append(key + " == " + json.dumps(value, ensure_ascii=False))
        return " and ".join(conditions)

    def rebuild(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> dict[str, object]:
        if not chunks or len(chunks) != len(vectors):
            raise ValueError("chunk/vector 数量不匹配")
        if len({c.chunk_id for c in chunks}) != len(chunks) or any(c.corpus_version != self.version for c in chunks):
            raise ValueError("重复 chunkId 或语料版本错误")
        dim = len(vectors[0])
        if dim < 2 or any(len(v) != dim or not all(math.isfinite(x) for x in v) for v in vectors):
            raise ValueError("向量维度或数值不合法")
        rows = [{
            "chunk_id": c.chunk_id, "doc_id": c.doc_id, "corpus_hash": self.corpus_hash,
            "text": c.title + "\n" + c.text, "component": c.component,
            "source": c.source, "source_revision": c.source_revision,
            "embedding": [float(x) for x in v],
        } for c, v in zip(chunks, vectors, strict=True)]
        if any(len(row["text"].encode()) > 65535 for row in rows):
            raise ValueError("Milvus text 超过 UTF-8 字节上限")
        from pymilvus import DataType, Function, FunctionType, MilvusClient

        exists = self._client.has_collection(self.collection)
        if not exists:
            schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
            for name, size in (("doc_id", 128), ("corpus_hash", 64), ("component", 128),
                               ("source", 4096), ("source_revision", 256)):
                schema.add_field(name, DataType.VARCHAR, max_length=size)
            schema.add_field("text", DataType.VARCHAR, max_length=65535, enable_analyzer=True,
                             analyzer_params={"type": "standard"})
            schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
            schema.add_function(Function(name="text_bm25", input_field_names=["text"],
                                         output_field_names=["sparse"], function_type=FunctionType.BM25))
            indexes = self._client.prepare_index_params()
            indexes.add_index(field_name="embedding", index_type="FLAT", metric_type="COSINE")
            indexes.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
            self._client.create_collection(collection_name=self.collection, schema=schema,
                                           index_params=indexes, consistency_level="Strong")
        # 重跑可以补全同一内容快照的中断导入；不 drop 旧 collection。
        for start in range(0, len(rows), 100):
            self._client.upsert(collection_name=self.collection, data=rows[start:start + 100])
        self._client.flush(self.collection)
        self._client.load_collection(self.collection)
        self.validate(chunks)
        return {"collection": self.collection, "chunkCount": len(chunks), "dimension": dim,
                "corpusHash": self.corpus_hash, "embeddingId": self.embedding_id,
                "denseIndex": "FLAT/COSINE", "lexicalIndex": "SPARSE_INVERTED_INDEX/BM25",
                "analyzer": "standard", "resumedExistingSnapshot": exists}

    def count(self) -> int:
        rows = self._client.query(collection_name=self.collection, filter=self._filter(None),
                                  output_fields=["count(*)"], consistency_level="Strong")
        return int(rows[0]["count(*)"])

    def validate(self, chunks: Sequence[Chunk]) -> None:
        if not self._client.has_collection(self.collection) or self.count() != len(chunks):
            raise RuntimeError("Milvus 快照不完整；先运行 rebuild")
        # MVP 语料有界，验证 ID 集而非仅凭数量接受错误快照。
        iterator = self._client.query_iterator(collection_name=self.collection, filter=self._filter(None),
                                               output_fields=["chunk_id"], batch_size=1000)
        ids: set[str] = set()
        try:
            while batch := iterator.next():
                ids.update(row["chunk_id"] for row in batch)
        finally:
            iterator.close()
        if ids != {c.chunk_id for c in chunks}:
            raise RuntimeError("Milvus 索引 ID 与固定快照不一致")

    def search(self, vector: Sequence[float], limit: int = 20, *, filters: Mapping[str, str] | None = None) -> list[SearchHit]:
        if len(vector) < 2 or not all(math.isfinite(x) for x in vector):
            raise ValueError("查询向量不合法")
        return self._search([float(x) for x in vector], "embedding", "COSINE", limit, filters)

    def _search(self, query: Any, field: str, metric: str, limit: int,
                filters: Mapping[str, str] | None) -> list[SearchHit]:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1..100")
        response = self._client.search(collection_name=self.collection, data=[query], anns_field=field,
                                       filter=self._filter(filters), limit=limit,
                                       output_fields=["chunk_id", "corpus_hash"],
                                       search_params={"metric_type": metric, "params": {}},
                                       consistency_level="Strong")
        if not isinstance(response, list) or len(response) != 1:
            raise RuntimeError("Milvus search 响应格式错误")
        hits = []
        for item in response[0]:
            entity = item.get("entity", {})
            if entity.get("corpus_hash") != self.corpus_hash:
                raise RuntimeError("Milvus 返回不同语料版本")
            score = float(item["distance"])
            if not math.isfinite(score):
                raise RuntimeError("Milvus 分数不合法")
            hits.append(SearchHit(str(entity["chunk_id"]), score))
        return hits
