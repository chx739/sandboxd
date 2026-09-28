"""本地离线记忆命令：不启动 Agent，不调用外部模型。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .app.memory import ExplicitExtractor, MemoryStore
from .app.runtime.session import SessionJournal


async def _run(args: argparse.Namespace) -> object:
    store = MemoryStore(args.root, args.project)
    if args.command == "extract":
        journal = SessionJournal(args.session_dir, args.session_id)
        result = await store.extract_session(journal, ExplicitExtractor())
        return result.to_dict()
    if args.command == "rebuild":
        return store.consolidate(summary_limit=args.summary_chars)
    if args.command == "forget":
        return store.forget(args.session_id)
    if args.command == "summary":
        return store.read_summary(args.limit)
    if args.command == "detail":
        return store.read_detail(args.limit)
    if args.command == "rollout":
        return store.read_rollout_summary(args.session_id, args.limit)
    raise ValueError("未知命令")


def main() -> None:
    parser = argparse.ArgumentParser(description="sandboxd 离线分层记忆")
    parser.add_argument(
        "--root", type=Path,
        default=Path.home() / ".local/share/sandboxd/memory",
        help="记忆根目录；默认 WSL 原生 home",
    )
    parser.add_argument("--project", default="sandboxd")
    actions = parser.add_subparsers(dest="command", required=True)
    extract = actions.add_parser("extract", help="从成功结束的 Session 提取显式记忆")
    extract.add_argument("--session-dir", type=Path, required=True)
    extract.add_argument("session_id")
    rebuild = actions.add_parser("rebuild", help="从所有 stage_one 文件重建层级记忆")
    rebuild.add_argument("--summary-chars", type=int, default=2048)
    forget = actions.add_parser("forget", help="忘记一个 Session 并重建")
    forget.add_argument("session_id")
    summary = actions.add_parser("summary", help="有界读取记忆摘要")
    summary.add_argument("--limit", type=int, default=2048)
    detail = actions.add_parser("detail", help="按需有界读取 MEMORY.md")
    detail.add_argument("--limit", type=int, default=16 << 10)
    rollout = actions.add_parser("rollout", help="按需读取指定会话摘要")
    rollout.add_argument("session_id")
    rollout.add_argument("--limit", type=int, default=2048)
    args = parser.parse_args()
    try:
        value = asyncio.run(_run(args))
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    if isinstance(value, str):
        print(value, end="" if value.endswith("\n") else "\n")
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
