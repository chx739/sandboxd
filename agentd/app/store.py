from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage

from .models import AgentTask, AlertEvent, utc_now
from .redaction import public_error
from .runner import AgentRunner
from .runtime.control import AgentControl
from .runtime.session import SessionJournal

_TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "limit_exceeded",
    "cancelled",
}
ControlKind = Literal["steer", "follow-up"]


class QueueFullError(RuntimeError):
    pass


class TaskNotFoundError(LookupError):
    pass


class TaskConflictError(RuntimeError):
    pass


class TaskStore:
    """单 Worker 的任务、Session 和运行控制身份表。

    这不是生产级调度器。内存表刻意保持简单，让面试时能清楚解释：
    taskId 标识一次运行，sessionId 标识线性事故会话，sandboxId 只存在于一次
    Runner 生命周期；steer/follow-up 通过 taskId 找到对应 AgentControl。
    """

    def __init__(self, trace_dir: Path) -> None:
        self._trace_dir = trace_dir
        self._session_dir = trace_dir / "sessions"
        self._tasks: dict[str, AgentTask] = {}
        self._dedupe: dict[str, tuple[str, datetime]] = {}
        self._controls: dict[str, AgentControl] = {}
        self._journals: dict[str, SessionJournal] = {}
        self._resume_messages: dict[str, list[BaseMessage]] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._cancel_requested: set[str] = set()
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        alert: AlertEvent,
        *,
        session_id: str | None = None,
        resume_messages: list[BaseMessage] | None = None,
        dedupe: bool = True,
    ) -> AgentTask:
        async with self._lock:
            self._prune_locked()
            fingerprint = alert.fingerprint or _fingerprint(alert)
            alert = alert.model_copy(update={"fingerprint": fingerprint})

            existing = self._dedupe.get(fingerprint) if dedupe else None
            if existing:
                task_id, created_at = existing
                if utc_now() - created_at < timedelta(minutes=10):
                    task = self._tasks.get(task_id)
                    if task:
                        return task.model_copy(deep=True)

            if self._queue.full():
                raise QueueFullError("Agent 任务队列已满")

            task_id = "task-" + secrets.token_hex(8)
            actual_session_id = session_id or "session-" + secrets.token_hex(8)
            journal = SessionJournal(self._session_dir, actual_session_id)
            # Header 必须先落盘再入队，避免 Worker 抢先写 Transcript。
            await journal.initialize(task_id, alert)

            task = AgentTask(
                taskId=task_id,
                sessionId=actual_session_id,
                status="queued",
                alert=alert,
            )
            self._tasks[task_id] = task
            self._controls[task_id] = AgentControl()
            self._journals[task_id] = journal
            if resume_messages:
                self._resume_messages[task_id] = list(resume_messages)
            if dedupe:
                self._dedupe[fingerprint] = (task_id, task.created_at)
            self._queue.put_nowait(task_id)
            return task.model_copy(deep=True)

    async def resume(self, session_id: str) -> AgentTask:
        journal = SessionJournal(self._session_dir, session_id)
        try:
            alert, messages = await journal.load_for_resume()
        except FileNotFoundError as exc:
            raise TaskNotFoundError("session not found") from exc

        # Provider 不能从 assistant 结尾直接 continue，因此追加一个普通用户消息。
        # 它不是 System 权限，也不会复用旧 Sandbox。
        messages.append(
            HumanMessage(
                content="请基于以上事故会话继续诊断，并输出更新后的结构化结论。"
            )
        )
        return await self.enqueue(
            alert,
            session_id=session_id,
            resume_messages=messages,
            dedupe=False,
        )

    async def send_control(
        self,
        task_id: str,
        kind: ControlKind,
        content: str,
    ) -> AgentTask:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError("task not found")
            if task.status not in {"queued", "running"}:
                raise TaskConflictError("task is not accepting control messages")

            journal = self._journals[task_id]
            await journal.append_command(task_id, kind, content)
            if kind == "steer":
                self._controls[task_id].steer(content)
            else:
                self._controls[task_id].follow_up(content)
            return task.model_copy(deep=True)

    async def cancel(self, task_id: str) -> AgentTask:
        running: asyncio.Task | None = None
        journal: SessionJournal
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError("task not found")
            if task.status in _TERMINAL_STATUSES:
                raise TaskConflictError("task already finished")

            self._cancel_requested.add(task_id)
            journal = self._journals[task_id]
            await journal.append_command(task_id, "cancel")
            if task.status == "queued":
                task.status = "cancelled"
            else:
                task.status = "cancelling"
                running = self._running.get(task_id)
            task.updated_at = utc_now()
            result = task.model_copy(deep=True)

        if running is not None:
            running.cancel()
        return result

    async def get(self, task_id: str) -> AgentTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            return task.model_copy(deep=True) if task else None

    async def get_session(self, session_id: str) -> dict:
        journal = SessionJournal(self._session_dir, session_id)
        try:
            return await journal.summary()
        except FileNotFoundError as exc:
            raise TaskNotFoundError("session not found") from exc

    async def list_recent(self) -> list[AgentTask]:
        async with self._lock:
            self._prune_locked()
            tasks = sorted(
                self._tasks.values(),
                key=lambda task: task.created_at,
                reverse=True,
            )
            return [task.model_copy(deep=True) for task in tasks]

    async def worker(self, runner: AgentRunner) -> None:
        while True:
            task_id = await self._queue.get()
            run_task: asyncio.Task | None = None
            journal: SessionJournal | None = None
            try:
                async with self._lock:
                    task = self._tasks.get(task_id)
                    if task is None:
                        continue
                    journal = self._journals[task_id]
                    if task.status in {"cancelled", "cancelling"}:
                        task.status = "cancelled"
                        task.updated_at = utc_now()
                        skip = True
                    else:
                        task.status = "running"
                        task.updated_at = utc_now()
                        skip = False
                        control = self._controls[task_id]
                        resume_messages = self._resume_messages.pop(task_id, None)
                        task_snapshot = task.model_copy(deep=True)

                if skip:
                    await journal.append_result(task_id, "cancelled")
                    continue

                run_task = asyncio.create_task(
                    runner.run(
                        task_id,
                        task_snapshot.alert,
                        control=control,
                        resume_messages=resume_messages,
                        journal=journal,
                    )
                )
                async with self._lock:
                    self._running[task_id] = run_task
                    cancel_now = task_id in self._cancel_requested
                if cancel_now:
                    run_task.cancel()

                try:
                    diagnosis, trace, status = await run_task
                    async with self._lock:
                        stored = self._tasks[task_id]
                        stored.status = status
                        stored.result = diagnosis
                        stored.trace = trace
                        stored.updated_at = utc_now()
                    self._write_trace(task_id)
                    await journal.append_result(
                        task_id,
                        status,
                        diagnosis.summary,
                    )
                except asyncio.CancelledError:
                    worker_task = asyncio.current_task()
                    if worker_task is not None and worker_task.cancelling():
                        if run_task is not None:
                            run_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await run_task
                        raise
                    await self._set_status(task_id, "cancelled")
                    await journal.append_result(task_id, "cancelled")
                except TimeoutError:
                    await self._set_error(
                        task_id,
                        "limit_exceeded",
                        "Agent 总执行时间超过上限",
                    )
                    await journal.append_result(task_id, "limit_exceeded")
                except Exception as exc:
                    error = "%s: %s" % (type(exc).__name__, public_error(exc))
                    await self._set_error(task_id, "failed", error)
                    await journal.append_result(task_id, "failed", error)
            finally:
                async with self._lock:
                    self._running.pop(task_id, None)
                    self._cancel_requested.discard(task_id)
                self._queue.task_done()

    async def _set_status(self, task_id: str, status: str) -> None:
        async with self._lock:
            task = self._tasks[task_id]
            task.status = status  # type: ignore[assignment]
            task.updated_at = utc_now()

    async def _set_error(self, task_id: str, status: str, error: str) -> None:
        async with self._lock:
            task = self._tasks[task_id]
            task.status = status  # type: ignore[assignment]
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
            if task.updated_at < cutoff and task.status in _TERMINAL_STATUSES
        ]
        for task_id in removable:
            self._tasks.pop(task_id, None)
            self._controls.pop(task_id, None)
            self._journals.pop(task_id, None)
            self._resume_messages.pop(task_id, None)

        known_tasks = set(self._tasks)
        self._dedupe = {
            fingerprint: value
            for fingerprint, value in self._dedupe.items()
            if value[0] in known_tasks
            and utc_now() - value[1] < timedelta(hours=1)
        }

        if len(self._tasks) <= 100:
            return
        completed = sorted(
            (
                task
                for task in self._tasks.values()
                if task.status in _TERMINAL_STATUSES
            ),
            key=lambda task: task.updated_at,
        )
        for task in completed[: len(self._tasks) - 100]:
            self._tasks.pop(task.task_id, None)
            self._controls.pop(task.task_id, None)
            self._journals.pop(task.task_id, None)
            self._resume_messages.pop(task.task_id, None)


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
