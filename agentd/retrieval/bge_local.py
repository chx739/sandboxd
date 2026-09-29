"""固定本地 E5/BGE embedding 与 BGE cross-encoder 重排。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence


class LocalBGE:
    def __init__(self, embedding_dir: Path, reranker_dir: Path) -> None:
        if not (embedding_dir / "model.safetensors").is_file():
            raise FileNotFoundError(f"BGE embedding 权重不存在: {embedding_dir}")
        if not (reranker_dir / "model.safetensors").is_file():
            raise FileNotFoundError(f"BGE reranker 权重不存在: {reranker_dir}")
        # 可选依赖只在检索 CLI 启动时加载；普通 Agent Runtime 无需安装 Torch。
        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(min(4, torch.get_num_threads()))
        self.embedding = SentenceTransformer(str(embedding_dir), device="cpu")
        self._reranker_dir = reranker_dir
        self._reranker = None
        self.dimension = int(self.embedding.get_sentence_embedding_dimension())
        self._e5 = embedding_dir.name.startswith("multilingual-e5-small-")

    def encode_passages(self, texts: Sequence[str], batch_size: int = 32) -> list[list[float]]:
        if not texts:
            return []
        return self._encode(["passage: " + text for text in texts] if self._e5 else texts, batch_size)

    def _encode(self, texts: Sequence[str], batch_size: int) -> list[list[float]]:
        vectors = self.embedding.encode(
            list(texts), batch_size=batch_size,
            normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [row.tolist() for row in vectors]

    def encode_query(self, query: str) -> list[float]:
        if not query.strip():
            raise ValueError("空 query")
        # BGE 英文检索的官方 query instruction；语料本身不加这个前缀。
        prefix = "query: " if self._e5 else "Represent this sentence for searching relevant passages: "
        return self._encode([prefix + query], batch_size=1)[0]

    def rerank(self, query: str, texts: Sequence[str], batch_size: int = 8) -> list[float]:
        if not texts:
            return []
        if self._reranker is None:
            from sentence_transformers import CrossEncoder
            self._reranker = CrossEncoder(
                str(self._reranker_dir), device="cpu", max_length=512
            )
        scores = self._reranker.predict(
            [[query, text] for text in texts],
            batch_size=batch_size,
            show_progress_bar=False,
        )
        return [float(value) for value in scores]
