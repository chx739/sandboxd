from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from .graph import AgentRunner
from .models import AgentTask, AlertEvent, utc_now


class QueueFullError(RuntimeError):
    pass


class TaskStore:
    def __init__(self, trace_dir: Path) -> None:
        self._trace_dir = trace_dir
        self._tasks: dict[str, AgentTask] = {}
        self._dedupe: dict[str, tuple[str, datetime]] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        self._lock = asyncio.Lock()

    async def enqueue(self, alert: AlertEvent) -> AgentTask:
        async with self._lock:
            self._prune_locked()
            fingerprint = alert.fingerprint or _fingerprint(alert)
            alert = alert.model_copy(update={"fingerprint": fingerprint})

            existing = self._dedupe.get(fingerprint)
            if existing:
                task_id, created_at = existing
                if utc_now() - created_at < timedelta(minutes=10):
                    task = self._tasks.get(task_id)
                    if task:
                        return task.model_copy(deep=True)

            if self._queue.full():
                raise QueueFullError("Agent 任务队列已满")

            task_id = "task-" + secrets.token_hex(8)
            task = AgentTask(taskId=task_id, status="queued", alert=alert)
            self._tasks[task_id] = task
            self._dedupe[fingerprint] = (task_id, task.created_at)
            self._queue.put_nowait(task_id)
            return task.model_copy(deep=True)

    async def get(self, task_id: str) -> AgentTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            return task.model_copy(deep=True) if task else None

    async def worker(self, runner: AgentRunner) -> None:
        while True:
            task_id = await self._queue.get()
            try:
                await self._set_status(task_id, "running")
                task = await self.get(task_id)
                if task is None:
                    continue
                try:
                    diagnosis, trace, status = await runner.run(task_id, task.alert)
                    async with self._lock:
                        stored = self._tasks[task_id]
                        stored.status = status
                        stored.result = diagnosis
                        stored.trace = trace
                        stored.updated_at = utc_now()
                    self._write_trace(task_id)
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    await self._set_error(
                        task_id,
                        "limit_exceeded",
                        "Agent 总执行时间超过上限",
                    )
                except Exception as exc:
                    await self._set_error(
                        task_id,
                        "failed",
                        "%s: %s" % (type(exc).__name__, str(exc)[:512]),
                    )
            finally:
                self._queue.task_done()

    async def _set_status(self, task_id: str, status: str) -> None:
        async with self._lock:
            task = self._tasks[task_id]
            task.status = status
            task.updated_at = utc_now()

    async def _set_error(self, task_id: str, status: str, error: str) -> None:
        async with self._lock:
            task = self._tasks[task_id]
            task.status = status
            task.error = error
            task.updated_at = utc_now()

    def _write_trace(self, task_id: str) -> None:
        task = self._tasks[task_id]
        if task.trace is None:
            return
        self._trace_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self._trace_dir / (task_id + ".json")
        target.write_text(
            task.trace.model_dump_json(by_alias=True, indent=2),
            encoding="utf-8",
        )
        target.chmod(0o600)

    def _prune_locked(self) -> None:
        cutoff = utc_now() - timedelta(hours=1)
        removable = [
            task_id
            for task_id, task in self._tasks.items()
            if task.updated_at < cutoff
            and task.status in {"succeeded", "failed", "limit_exceeded"}
        ]
        for task_id in removable:
            self._tasks.pop(task_id, None)

        known_tasks = set(self._tasks)
        self._dedupe = {
            fingerprint: value
            for fingerprint, value in self._dedupe.items()
            if value[0] in known_tasks and utc_now() - value[1] < timedelta(hours=1)
        }

        if len(self._tasks) <= 100:
            return
        completed = sorted(
            (
                task
                for task in self._tasks.values()
                if task.status in {"succeeded", "failed", "limit_exceeded"}
            ),
            key=lambda task: task.updated_at,
        )
        for task in completed[: len(self._tasks) - 100]:
            self._tasks.pop(task.task_id, None)


def _fingerprint(alert: AlertEvent) -> str:
    stable = json.dumps(
        {
            "labels": alert.labels,
            "startsAt": alert.starts_at.isoformat() if alert.starts_at else "",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(stable.encode()).hexdigest()[:32]
