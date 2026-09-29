"""Milvus 2.5.10 的本地 dense 向量索引；小语料用精确 FLAT/COSINE。"""

from __future__ import annotations

import math
import re
from typing import Any, Sequence

from .core import Chunk, SearchHit

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def collection_name(version: str, corpus_hash: str) -> str:
    if not _VERSION.fullmatch(version) or not re.fullmatch(r"[a-f0-9]{64}", corpus_hash):
        raise ValueError("语料版本或 hash 不合法")
    return f"sandboxd_rag_{version.lower().replace('-', '_')}_{corpus_hash[:12]}"


class MilvusDense:
    def __init__(
        self,
        version: str,
        corpus_hash: str,
        *,
        uri: str = "http://127.0.0.1:19530",
        client: Any | None = None,
    ) -> None:
        if uri not in {"http://127.0.0.1:19530", "http://localhost:19530"}:
            raise ValueError("Demo Milvus 仅连接本机服务")
        self.collection = collection_name(version, corpus_hash)
        self.version = version
        self.corpus_hash = corpus_hash
        if client is None:
            from pymilvus import MilvusClient
            client = MilvusClient(uri=uri, timeout=30)
        self._client = client

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MilvusDense":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def rebuild(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> dict[str, object]:
        if not chunks or len(chunks) != len(vectors):
            raise ValueError("chunk/vector 数量不匹配")
        if any(chunk.corpus_version != self.version for chunk in chunks):
            raise ValueError("语料版本不匹配")
        dim = len(vectors[0])
        if dim < 2 or any(len(vector) != dim or not all(math.isfinite(x) for x in vector) for vector in vectors):
            raise ValueError("向量维度或数值不合法")
        from pymilvus import DataType, MilvusClient

        if self._client.has_collection(self.collection):
            # 只重建由版本和 hash 命名的本项目 collection。
            self._client.drop_collection(self.collection)
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=128)
        schema.add_field("corpus_hash", DataType.VARCHAR, max_length=64)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="embedding", index_type="FLAT", metric_type="COSINE"
        )
        self._client.create_collection(
            collection_name=self.collection, schema=schema, index_params=index_params,
        )
        for start in range(0, len(chunks), 100):
            self._client.insert(self.collection, [
                {
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "corpus_hash": self.corpus_hash,
                    "embedding": [float(value) for value in vector],
                }
                for chunk, vector in zip(
                    chunks[start:start + 100], vectors[start:start + 100], strict=True
                )
            ])
        self._client.flush(self.collection)
        self._client.load_collection(self.collection)
        count = self.count()
        if count != len(chunks):
            raise RuntimeError(f"Milvus count={count}，期望 {len(chunks)}")
        return {
            "collection": self.collection, "chunkCount": count,
            "dimension": dim, "corpusHash": self.corpus_hash,
            "indexType": "FLAT", "metricType": "COSINE",
        }

    def count(self) -> int:
        return int(self._client.get_collection_stats(self.collection)["row_count"])

    def search(self, vector: Sequence[float], limit: int = 20) -> list[SearchHit]:
        if len(vector) < 2 or not all(math.isfinite(value) for value in vector):
            raise ValueError("查询向量不合法")
        if not 1 <= limit <= 100:
            raise ValueError("limit 超出范围")
        response = self._client.search(
            collection_name=self.collection,
            data=[[float(value) for value in vector]],
            anns_field="embedding",
            filter=f'corpus_hash == "{self.corpus_hash}"',
            limit=limit,
            output_fields=["chunk_id", "corpus_hash"],
            search_params={"metric_type": "COSINE", "params": {}},
        )
        if not isinstance(response, list) or len(response) != 1:
            raise RuntimeError("Milvus search 响应格式错误")
        result = []
        for item in response[0]:
            entity = item.get("entity", {})
            if entity.get("corpus_hash") != self.corpus_hash:
                raise RuntimeError("Milvus 返回了不同语料版本")
            chunk_id = str(entity.get("chunk_id") or item.get("id"))
            result.append(SearchHit(chunk_id, float(item["distance"])))
        return result
