"""重建、查询与消融测评 ES BM25 + Milvus dense + RRF + BGE。"""

from __future__ import annotations

import argparse
import json
import resource
import time
from contextlib import ExitStack
from pathlib import Path

from .bge_local import LocalBGE
from .core import evaluate_rankings, load_dataset
from .elasticsearch_bm25 import ElasticsearchBM25
from .milvus_dense import MilvusDense
from .pipeline import HybridRetriever

_ROOT = Path(__file__).resolve().parent
_MODELS = Path.home() / ".local/share/sandboxd/models"


def _run(args: argparse.Namespace) -> dict:
    chunks, cases, digest = load_dataset(args.corpus, args.queries)
    version = chunks[0].corpus_version
    if args.command == "check":
        return {
            "corpusVersion": version,
            "corpusHash": digest,
            "chunkCount": len(chunks),
            "queryCount": len(cases),
            "labelCount": sum(len(case.relevance) for case in cases),
        }

    model = LocalBGE(args.embedding_dir, args.reranker_dir)
    with ExitStack() as stack:
        es = stack.enter_context(ElasticsearchBM25(version, digest))
        milvus = stack.enter_context(MilvusDense(version, digest))
        if args.command == "rebuild":
            started = time.monotonic()
            vectors = model.encode_passages([
                chunk.title + "\n" + chunk.text for chunk in chunks
            ])
            embedded = time.monotonic()
            es_report = es.rebuild(chunks)
            es_done = time.monotonic()
            milvus_report = milvus.rebuild(chunks, vectors)
            finished = time.monotonic()
            return {
                "corpusVersion": version, "corpusHash": digest,
                "es": es_report, "milvus": milvus_report,
                "embeddingMs": round((embedded - started) * 1000, 2),
                "esBuildMs": round((es_done - embedded) * 1000, 2),
                "milvusBuildMs": round((finished - es_done) * 1000, 2),
                "kind": "docker-integration-local-model",
            }
        if es.count() != len(chunks) or milvus.count() != len(chunks):
            raise RuntimeError("双索引数量不一致；先执行 rebuild")
        retriever = HybridRetriever(chunks, es, milvus, model)
        if args.command == "query":
            result = retriever.query(
                args.text, recall_limit=args.recall_limit,
                rerank_limit=args.rerank_limit, output_limit=args.top_k,
            )
            return {
                "corpusVersion": version, "corpusHash": digest,
                "query": args.text, "evidence": result.evidence,
                "rankings": result.rankings,
                "latenciesMs": {key: round(value, 2) for key, value in result.latencies_ms.items()},
                "kind": "docker-integration-local-model",
            }
        if args.command == "eval":
            selected = cases[:args.max_queries] if args.max_queries else cases
            rankings: dict[str, dict[str, list[str]]] = {
                group: {} for group in ("bm25", "dense", "rrf", "rerank")
            }
            latencies: dict[str, dict[str, float]] = {
                group: {} for group in rankings
            }
            for case in selected:
                result = retriever.query(
                    case.query, recall_limit=args.recall_limit,
                    rerank_limit=args.rerank_limit, output_limit=10,
                )
                for group in rankings:
                    rankings[group][case.query_id] = result.rankings[group]
                    latencies[group][case.query_id] = result.latencies_ms[group]
            return {
                "kind": "docker-integration-local-model",
                "corpusVersion": version, "corpusHash": digest,
                "chunkCount": len(chunks), "queryCount": len(selected),
                "models": {
                    "embedding": str(args.embedding_dir),
                    "reranker": str(args.reranker_dir),
                },
                "results": {
                    group: evaluate_rankings(selected, rankings[group], latencies[group])
                    for group in rankings
                },
                "processMaxRssMB": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
            }
    raise ValueError("未知命令")


def main() -> None:
    parser = argparse.ArgumentParser(description="sandboxd 混合检索 MVP")
    parser.add_argument("--corpus", type=Path, default=_ROOT / "data/runbook-v1.corpus.jsonl")
    parser.add_argument("--queries", type=Path, default=_ROOT / "data/runbook-v1.queries.jsonl")
    parser.add_argument("--embedding-dir", type=Path, default=_MODELS / "bge-small-en-v1.5-5c38ec7")
    parser.add_argument("--reranker-dir", type=Path, default=_MODELS / "bge-reranker-base-2cfc18c")
    actions = parser.add_subparsers(dest="command", required=True)
    actions.add_parser("check", help="不启动模型/服务，验证语料与标签")
    actions.add_parser("rebuild", help="重建本项目精确命名的两个索引")
    query = actions.add_parser("query", help="运行带来源的混合检索")
    query.add_argument("text")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--recall-limit", type=int, default=20)
    query.add_argument("--rerank-limit", type=int, default=20)
    evaluate = actions.add_parser("eval", help="同一标签集上做四组消融")
    evaluate.add_argument("--max-queries", type=int, default=0)
    evaluate.add_argument("--recall-limit", type=int, default=20)
    evaluate.add_argument("--rerank-limit", type=int, default=20)
    args = parser.parse_args()
    try:
        print(json.dumps(_run(args), ensure_ascii=False, indent=2))
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
