"""一次 attempt 的持久预算与完整会话边界；执行与恢复策略仍由 Runner 负责。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Sequence
from langchain_core.messages import BaseMessage, HumanMessage

from .recovery import RecoveryConflict, RecoveryLedger


class RecoveryAttempt:
    def __init__(self, ledger: RecoveryLedger, task_id: str, attempt_id: str) -> None:
        self.ledger = ledger
        self.task_id = task_id
        self.attempt_id = attempt_id
        state = self.snapshot()
        if (not state["attempts"] or state["attempts"][-1]["attemptId"] != attempt_id
                or state["attempts"][-1]["ownerStopped"]):
            raise RecoveryConflict("attempt is not active")

    def snapshot(self) -> dict[str, Any]:
        return self.ledger.get(self.task_id)

    def used(self, kind: str) -> int:
        return sum(call["kind"] == kind for call in self.snapshot()["calls"].values())

    def prometheus_used(self) -> int:
        return sum(call["kind"] == "tool" and call["operation"] == "query_prometheus"
                   for call in self.snapshot()["calls"].values())

    def remaining(self) -> float:
        return self.ledger.remaining_seconds(self.task_id, datetime.now(timezone.utc))

    def reserve(self, kind: str, *, name: str = "", arguments: dict[str, Any] | None = None) -> str:
        arguments = arguments or {}
        content = arguments.get("content")
        expected_hash = (
            hashlib.sha256(content.encode("utf-8")).hexdigest()
            if name == "write_file" and isinstance(content, str) else None
        )
        return self.ledger.reserve(
            self.task_id, self.attempt_id, kind, datetime.now(timezone.utc),
            operation=name, path=str(arguments.get("path", "")),
            expected_new_sha256=expected_hash,
        )

    def finish(self, call_id: str) -> None:
        self.ledger.finish_call(self.task_id, self.attempt_id, call_id)

    def boundary(self, node_id: str | None, messages: Sequence[BaseMessage] = ()) -> None:
        if node_id is not None:
            pending = {c["commandId"] for c in self.snapshot()["commands"] if c["appliedNodeId"] is None}
            command_ids = list(dict.fromkeys(
                m.additional_kwargs.get("sandboxdCommandId") for m in messages
                if isinstance(m, HumanMessage) and m.additional_kwargs.get("sandboxdCommandId") in pending
            ))
            self.ledger.complete_boundary(self.task_id, self.attempt_id, node_id, command_ids)
