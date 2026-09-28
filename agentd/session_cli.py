"""查看脱敏 Session 树；旧格式首次查看时会就地迁移，branch/resume 由 API 执行。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .app.runtime.session import SessionJournal


async def _run(args: argparse.Namespace) -> dict:
    journal = SessionJournal(args.session_dir, args.session_id)
    if args.command == "tree":
        return await journal.tree()
    if args.command == "path":
        return await journal.path_messages(args.node_id)
    raise ValueError("未知命令")


def main() -> None:
    parser = argparse.ArgumentParser(description="查看 sandboxd 脱敏 Session 树")
    parser.add_argument("--session-dir", type=Path, required=True)
    actions = parser.add_subparsers(dest="command", required=True)
    tree = actions.add_parser("tree", help="列出节点和活动叶子")
    tree.add_argument("session_id")
    path = actions.add_parser("path", help="读取指定节点的祖先消息")
    path.add_argument("session_id")
    path.add_argument("node_id")
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args))
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
