"""最小文档增改删：生成不可变的新语料快照，旧索引和文件保留。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import Chunk, load_corpus, load_jsonl


def revise(source: Path, output: Path, *, upsert: Path | None = None,
           delete: list[str] | None = None) -> dict:
    chunks, old_hash = load_corpus(source)
    deleting = set(delete or [])
    known = {chunk.doc_id for chunk in chunks}
    if not deleting <= known:
        raise ValueError("要删除的 docId 不存在")
    replacements = [Chunk.from_dict(item) for item in load_jsonl(upsert)] if upsert else []
    if upsert and not replacements:
        raise ValueError("更新文件为空")
    updated = {chunk.doc_id for chunk in replacements}
    if deleting & updated or not deleting and not updated:
        raise ValueError("增改删操作为空或互相冲突")
    if any(chunk.corpus_version != chunks[0].corpus_version for chunk in replacements):
        raise ValueError("更新语料版本不一致")
    # upsert 以 docId 为单位完全替换，避免旧章节残留。传入的是完整文档的所有 chunks。
    result = [chunk for chunk in chunks if chunk.doc_id not in deleting | updated] + replacements
    if not result or len({c.chunk_id for c in result}) != len(result):
        raise ValueError("结果为空或 chunkId 重复")
    # 只允许新目录；失败也不覆盖已有数据。
    output.mkdir(parents=True, exist_ok=False)
    path = output / "corpus.jsonl"
    path.write_text("".join(json.dumps(c.to_dict(), ensure_ascii=False) + "\n" for c in sorted(result, key=lambda c: c.chunk_id)))
    _, digest = load_corpus(path)
    report = {"parentCorpusHash": old_hash, "corpusHash": digest,
              "deletedDocIds": sorted(deleting), "upsertedDocIds": sorted(updated),
              "documentCount": len({c.doc_id for c in result}), "chunkCount": len(result),
              "indexStatus": "pending-rebuild", "oldSnapshotsRetained": True,
              "evaluationLabels": "not-copied; revalidate labels for this new corpus",
              "source": str(source.resolve())}
    (output / "revision.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upsert", type=Path, help="完整替换文档的 chunks JSONL")
    parser.add_argument("--delete", action="append", default=[], help="docId；可重复")
    args = parser.parse_args()
    print(json.dumps(revise(args.corpus, args.output, upsert=args.upsert, delete=args.delete), indent=2))
