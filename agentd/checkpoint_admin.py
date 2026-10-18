"""离线检查点管理：只读预览与显式pin；本入口不执行forget/prune。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .app.runtime.checkpoints import CheckpointLedger
from .app.runtime.session_refs import SessionReferences


def perform(directory: Path, command: str, checkpoint_id: str | None = None) -> dict:
    if command not in {"preview", "pin", "unpin"}:
        raise ValueError("unknown fixed checkpoint management command")
    if not directory.is_dir() or not (directory / "checkpoints.jsonl").is_file() or not (directory / "session-refs").is_dir():
        raise ValueError("requires existing checkpoint control directory; no implicit initialization")
    ledger = CheckpointLedger(directory / "checkpoints.jsonl", SessionReferences(directory), read_only=command == "preview")
    try:
        if command != "preview":
            ledger.retain(checkpoint_id, command == "pin")
        return ledger.cleanup_preview()
    finally:
        ledger.close()


def main():
    parser = argparse.ArgumentParser(description="停止同目录agentd后进行本地检查点管理；不访问存储服务")
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    actions = parser.add_subparsers(dest="command", required=True)
    actions.add_parser("preview")
    for name in ("pin", "unpin"):
        actions.add_parser(name).add_argument("checkpoint_id")
    args = parser.parse_args()
    print(json.dumps(perform(args.checkpoint_dir, args.command, getattr(args, "checkpoint_id", None)),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
