"""将校验过的 BEIR SciFact 官方 zip 转成项目的显式语料/标签契约。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

from .core import Chunk, QueryCase, load_dataset

SOURCE_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
SOURCE_MD5 = "5f7d1de60b170fc8027bb7898e2efca1"
VERSION = "scifact-beir-test-v1"
_FILES = {"scifact/corpus.jsonl", "scifact/queries.jsonl", "scifact/qrels/test.tsv"}


def _jsonl(zipped: zipfile.ZipFile, name: str) -> list[dict]:
    rows = []
    for line in zipped.read(name).splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{name} 包含非对象 JSON")
        rows.append(value)
    return rows


def convert(source_zip: Path, output_dir: Path) -> dict[str, object]:
    digest = hashlib.md5(source_zip.read_bytes()).hexdigest()  # noqa: S324 - 官方公布 MD5 仅作数据集一致性校验
    if digest != SOURCE_MD5:
        raise ValueError("SciFact zip 与官方公布的 MD5 不一致")
    with zipfile.ZipFile(source_zip) as zipped:
        if not _FILES <= set(zipped.namelist()):
            raise ValueError("SciFact zip 缺少必需文件")
        if any(zipped.getinfo(name).file_size > 16_000_000 for name in _FILES):
            raise ValueError("SciFact 文件超出 Demo 上限")
        corpus = _jsonl(zipped, "scifact/corpus.jsonl")
        query_rows = _jsonl(zipped, "scifact/queries.jsonl")
        qrels = csv.DictReader(io.StringIO(zipped.read("scifact/qrels/test.tsv").decode()), delimiter="\t")
        relevance: dict[str, dict[str, int]] = defaultdict(dict)
        for row in qrels:
            query_id, doc_id, grade = row["query-id"], row["corpus-id"], int(row["score"])
            if grade > 0:
                relevance[query_id][f"scifact:{doc_id}"] = grade
    chunks = [Chunk.from_dict({
        "chunkId": f"scifact:{row['_id']}",
        "docId": str(row["_id"]),
        "title": row.get("title", ""),
        "text": row["text"],
        "source": f"{SOURCE_URL}#docId={row['_id']}",
        "corpusVersion": VERSION,
    }) for row in corpus]
    queries = {str(row["_id"]): row["text"] for row in query_rows}
    cases = [QueryCase.from_dict({
        "queryId": f"scifact:q{query_id}",
        "query": queries[query_id],
        "relevance": labels,
    }) for query_id, labels in sorted(relevance.items(), key=lambda pair: int(pair[0]))]
    output_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = output_dir / "scifact.corpus.jsonl"
    queries_path = output_dir / "scifact.queries.jsonl"
    corpus_path.write_text("".join(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n" for chunk in chunks), encoding="utf-8")
    queries_path.write_text("".join(json.dumps({
        "queryId": case.query_id, "query": case.query, "relevance": case.relevance,
    }, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    _, _, corpus_hash = load_dataset(corpus_path, queries_path)
    return {
        "sourceUrl": SOURCE_URL, "sourceMd5": digest, "split": "test",
        "corpusVersion": VERSION, "corpusHash": corpus_hash,
        "documentCount": len(chunks), "queryCount": len(cases),
        "corpus": str(corpus_path), "queries": str(queries_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="校验并转换 BEIR 官方 SciFact zip")
    parser.add_argument("source_zip", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(convert(args.source_zip, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
