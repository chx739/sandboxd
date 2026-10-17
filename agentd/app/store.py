from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections import deque
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage

from .models import AgentTask, AgentTrace, AgentEvent, AlertEvent, utc_now
from .redaction import public_error
from .runner import AgentRunner
from .runtime.control import AgentControl
from .runtime.session import SessionJournal
from .runtime.recovery import RecoveryLedger, RecoveryConflict, RecoveryLimit, new_id, TERMINAL
from .runtime.execution import RecoveryAttempt
from .tools.remote_files import WorkspaceExecutionError
import httpx
from .working_memory import project_working_memory

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

    部署入口传recovery_dir后，以持久账本重建内存投影并自动接续同Task。
    不传目录只用于历史离线夹具；不做分布式调度或高可用。
    """

    def __init__(self, trace_dir: Path, *, recovery_dir: Path | None = None) -> None:
        self._trace_dir = recovery_dir / "traces" if recovery_dir is not None else trace_dir
        self._session_dir = recovery_dir / "sessions" if recovery_dir is not None else trace_dir / "sessions"
        self._legacy_session_dir = trace_dir / "sessions"
        self._ledger = RecoveryLedger(recovery_dir / "tasks.jsonl") if recovery_dir is not None else None
        self._startup_queue: deque[str] = deque()
        self._tasks: dict[str, AgentTask] = {}
        self._dedupe: dict[str, tuple[str, datetime]] = {}
        self._controls: dict[str, AgentControl] = {}
        self._journals: dict[str, SessionJournal] = {}
        self._session_journals: dict[str, SessionJournal] = {}
        self._resume_messages: dict[str, list[BaseMessage]] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._cancel_requested: set[str] = set()
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        self._lock = asyncio.Lock()
        if self._ledger is not None:
            for state in self._ledger.records():
                task = AgentTask(
                    taskId=state["taskId"], sessionId=state["sessionId"], alert=AlertEvent.model_validate(state["alert"]),
                    status=state["status"] if state["status"] in TERMINAL else "queued",
                    createdAt=datetime.fromisoformat(self._ledger.created_at(state["taskId"])),
                )
                self._tasks[task.task_id] = task
                self._controls[task.task_id] = AgentControl()
                self._journals[task.task_id] = self._journal(task.session_id)
                if task.alert.fingerprint:
                    self._dedupe[task.alert.fingerprint] = (task.task_id, task.created_at)
                self._sync_durable_task(task.task_id)
                if state["status"] not in TERMINAL:
                    self._startup_queue.append(task.task_id)

    async def enqueue(
        self,
        alert: AlertEvent,
        *,
        session_id: str | None = None,
        resume_messages: list[BaseMessage] | None = None,
        parent_node_id: str | None = None,
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

            if self._queue.full() or (self._ledger is not None and sum(t.status not in TERMINAL for t in self._tasks.values()) >= 17):
                raise QueueFullError("Agent 任务队列已满")

            task_id = "task-" + secrets.token_hex(8)
            actual_session_id = session_id or "session-" + secrets.token_hex(8)
            journal = self._journal(actual_session_id)
            branch_id, workspace_id = new_id("branch"), new_id("workspace")
            if self._ledger is not None and session_id is not None:
                history = [s for s in self._ledger.records() if s["sessionId"] == session_id]
                if not history:
                    raise TaskConflictError("旧会话没有工作区映射，仅可读取上下文；请显式迁移后继续")
                previous = history[-1]
                branch_id, workspace_id = previous["branchId"], previous["workspaceId"]
                if any(s["workspaceId"] == workspace_id and s["status"] not in TERMINAL for s in history):
                    raise TaskConflictError("工作区已有未完成任务")
                if parent_node_id != (await journal.summary())["activeLeafId"]:
                    raise TaskConflictError("NFS V1 无对应历史文件版本，不能从旧节点恢复工作区")
            # Header 必须先落盘再入队，避免 Worker 抢先写 Transcript。
            await journal.initialize(task_id, alert, parent_node_id)
            if self._ledger is not None:
                self._ledger.create(task_id=task_id, session_id=actual_session_id, branch_id=branch_id,
                                    workspace_id=workspace_id, alert=alert.model_dump(mode="json", by_alias=True))

            task = AgentTask(
                taskId=task_id,
                sessionId=actual_session_id,
                status="queued",
                alert=alert,
            )
            if self._ledger is not None:
                task.created_at = datetime.fromisoformat(self._ledger.created_at(task_id))
            self._tasks[task_id] = task
            self._controls[task_id] = AgentControl()
            self._journals[task_id] = journal
            if self._ledger is not None:
                self._sync_durable_task(task_id)
            if resume_messages:
                self._resume_messages[task_id] = list(resume_messages)
            if dedupe:
                self._dedupe[fingerprint] = (task_id, task.created_at)
            self._queue.put_nowait(task_id)
            return task.model_copy(deep=True)

    async def resume(self, session_id: str, node_id: str | None = None) -> AgentTask:
        journal = self._journal(session_id)
        try:
            alert, messages, selected_node_id = await journal.load_branch(node_id)
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
            parent_node_id=selected_node_id,
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
            command_id = None
            if self._ledger is not None:
                command_id = self._ledger.queue_command(task_id, kind, content)
                content = self._ledger.get(task_id)["commands"][-1]["content"]
            await journal.append_command(task_id, kind, content)
            if kind == "steer":
                self._controls[task_id].steer(content, command_id)
            else:
                self._controls[task_id].follow_up(content, command_id)
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
            previous_status = task.status
            if self._ledger is not None:
                self._ledger.cancel(task_id)
            journal = self._journals[task_id]
            await journal.append_command(task_id, "cancel")
            if task.status == "queued":
                task.status = "cancelled"
            else:
                task.status = "cancelling"
                running = self._running.get(task_id)
                if running is not None and running.done():
                    running = None
                if self._ledger is not None and running is None and previous_status == "interrupted":
                    if self._queue.full():
                        self._startup_queue.append(task_id)
                    else:
                        self._queue.put_nowait(task_id)
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
        journal = self._journal(session_id)
        try:
            summary = await journal.summary()
            if self._ledger is not None:
                history = [s for s in self._ledger.records() if s["sessionId"] == session_id]
                if history:
                    summary.update({key: history[-1][key] for key in ("taskId", "branchId", "workspaceId", "status")})
            return summary
        except FileNotFoundError as exc:
            raise TaskNotFoundError("session not found") from exc

    async def get_session_tree(self, session_id: str) -> dict:
        try:
            tree = await self._journal(session_id).tree()
            if self._ledger is not None:
                history = [s for s in self._ledger.records() if s["sessionId"] == session_id]
                if history:
                    tree.update({key: history[-1][key] for key in ("branchId", "workspaceId")})
            return tree
        except FileNotFoundError as exc:
            raise TaskNotFoundError("session not found") from exc

    async def get_session_path(self, session_id: str, node_id: str) -> dict:
        try:
            return await self._journal(session_id).path_messages(node_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError("session not found") from exc

    async def get_session_working_memory(
        self, session_id: str, node_id: str | None = None,
    ) -> dict:
        try:
            alert, messages, selected = await self._journal(session_id).load_branch(node_id)
        except FileNotFoundError as exc:
            raise TaskNotFoundError("session not found") from exc
        return {
            "sessionId": session_id,
            "nodeId": selected,
            "workingMemory": project_working_memory(
                alert.model_dump(mode="json", by_alias=True), messages,
            ),
        }

    def _journal(self, session_id: str) -> SessionJournal:
        # 同一个进程内的多个 task 共用锁，避免分支写入互相穿插。
        journal = self._session_journals.get(session_id)
        if journal is None:
            # 先校验ID再做任何历史路径存在性检查。
            candidate = SessionJournal(self._session_dir, session_id)
            directory = self._session_dir
            if (self._ledger is not None and not (directory / (session_id + ".jsonl")).exists()
                    and (self._legacy_session_dir / (session_id + ".jsonl")).exists()):
                directory = self._legacy_session_dir
            journal = candidate if directory == self._session_dir else SessionJournal(directory, session_id)
            self._session_journals[session_id] = journal
        return journal

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
            from_startup = bool(self._startup_queue)
            task_id = self._startup_queue.popleft() if from_startup else await self._queue.get()
            run_task: asyncio.Task | None = None
            journal: SessionJournal | None = None
            try:
                if self._ledger is not None:
                    await self._run_durable_task(task_id, runner)
                    continue
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
                if not from_startup:
                    self._queue.task_done()

    async def _set_status(self, task_id: str, status: str) -> None:
        async with self._lock:
            task = self._tasks[task_id]
            task.status = status  # type: ignore[assignment]
            task.updated_at = utc_now()

    def _sync_durable_task(self, task_id: str) -> None:
        state = self._ledger.get(task_id)
        task = self._tasks[task_id]
        task.branch_id, task.workspace_id = state["branchId"], state["workspaceId"]
        task.attempt_count = len(state["attempts"])
        task.attempt_id = state["attempts"][-1]["attemptId"] if state["attempts"] else None
        traces = self._ledger.traces(task_id)
        if traces:
            task.trace = AgentTrace.model_validate(traces[-1])
            if task.status in TERMINAL:
                task.result = task.trace.final
            if task.status in {"failed", "interrupted", "limit_exceeded"}:
                for event in reversed(task.trace.events):
                    if event.type in {"agent.interrupted", "sandbox.release.blocked"}:
                        task.error = str(event.details.get("error", "attempt interrupted"))
                        break

    async def _finish_durable(self, task_id: str, status: str, error: str | None = None) -> None:
        state = self._ledger.finish(task_id, status)
        async with self._lock:
            task = self._tasks[task_id]
            task.status = state["status"]
            task.updated_at = utc_now()
            self._sync_durable_task(task_id)
            if error is not None:
                task.error = error
        await self._journals[task_id].append_result(task_id, task.status, error or (task.result.summary if task.result else ""))

    async def _run_durable_task(self, task_id: str, runner: AgentRunner) -> None:
        while True:
            state = self._ledger.get(task_id)
            if state["status"] in TERMINAL:
                async with self._lock:
                    self._tasks[task_id].status = state["status"]
                    self._sync_durable_task(task_id)
                return
            if state["attempts"] and not state["attempts"][-1]["ownerStopped"]:
                old = state["attempts"][-1]
                try:
                    await runner.stop_attempt(RecoveryAttempt(self._ledger, task_id, old["attemptId"]))
                except Exception as exc:
                    await self._set_error(task_id, "interrupted", "旧执行者未确认停止：" + public_error(exc))
                    return
                # 强杀进程可能没有 finally Trace；只补控制面中断事实，不编造模型用量。
                if not any(t["attemptId"] == old["attemptId"] for t in self._ledger.traces(task_id)):
                    provider = state["provider"] or {}
                    self._ledger.record_trace(AgentTrace(
                        taskId=task_id, attemptId=old["attemptId"], workspaceId=state["workspaceId"],
                        mode="recovery", model=provider.get("model", "not-selected"),
                        provider=provider.get("provider", "unknown"), sandboxId=self._ledger.get(task_id)["attempts"][-1]["sandboxId"],
                        verdict="process-interrupted", status="interrupted",
                        events=[AgentEvent(index=1, type="attempt.recovered", details={"usageKnown": False,
                                           "reason": "prior attempt had no persisted completion trace"})],
                    ))
                state = self._ledger.get(task_id)
            if state["cancelRequested"]:
                await self._finish_durable(task_id, "cancelled")
                return
            traces = self._ledger.traces(task_id)
            if (traces and traces[-1].get("status") in TERMINAL
                    and state["attempts"][-1]["ownerStopped"]):
                await self._finish_durable(task_id, traces[-1]["status"])
                return
            try:
                state = self._ledger.begin_attempt(task_id, utc_now())
            except RecoveryLimit as exc:
                await self._finish_durable(task_id, "limit_exceeded", public_error(exc))
                return
            recovery = RecoveryAttempt(self._ledger, task_id, state["attempts"][-1]["attemptId"])
            async with self._lock:
                task = self._tasks[task_id]
                task.status, task.error, task.updated_at = "running", None, utc_now()
                self._sync_durable_task(task_id)
                control = AgentControl()
                for command in state["commands"]:
                    if command["appliedNodeId"] is None:
                        if command["kind"] == "steer":
                            control.steer(command["content"], command["commandId"])
                        else:
                            control.follow_up(command["content"], command["commandId"])
                self._controls[task_id] = control
                run_task = asyncio.create_task(runner.run(
                    task_id, task.alert, control=control, journal=self._journals[task_id], recovery=recovery,
                    resume_messages=self._resume_messages.pop(task_id, None),
                ))
                self._running[task_id] = run_task
                if self._ledger.get(task_id)["cancelRequested"]:
                    run_task.cancel()
            try:
                _, _, status = await run_task
            except asyncio.CancelledError:
                worker = asyncio.current_task()
                if worker is not None and worker.cancelling():
                    raise  # 服务退出不写 user cancel；下一次启动仍可接续。
                state = self._ledger.get(task_id)
                if state["cancelRequested"] and state["attempts"][-1]["ownerStopped"]:
                    await self._finish_durable(task_id, "cancelled")
                else:
                    await self._set_error(task_id, "interrupted", "执行已中断，资源状态待核查")
                return
            except Exception as exc:
                worker = asyncio.current_task()
                if worker is not None and worker.cancelling():
                    raise asyncio.CancelledError from exc
                state = self._ledger.get(task_id)
                if not state["attempts"][-1]["ownerStopped"]:
                    await self._set_error(task_id, "interrupted", "旧执行者未确认停止：" + public_error(exc))
                    return
                if state["cancelRequested"]:
                    await self._finish_durable(task_id, "cancelled")
                    return
                if isinstance(exc, httpx.HTTPError) or (isinstance(exc, WorkspaceExecutionError) and exc.recoverable):
                    # 新 attempt 由同一 Task 的 begin_attempt 发放，原额度与时钟不变。
                    continue
                status = "limit_exceeded" if isinstance(exc, (TimeoutError, RecoveryLimit)) else "failed"
                await self._finish_durable(task_id, status, public_error(exc))
                return
            await self._finish_durable(task_id, status)
            return

    async def get_task_traces(self, task_id: str) -> list[dict]:
        async with self._lock:
            if task_id not in self._tasks:
                raise TaskNotFoundError("task not found")
            if self._ledger is not None:
                return self._ledger.traces(task_id)
            trace = self._tasks[task_id].trace
            return [trace.model_dump(mode="json", by_alias=True)] if trace is not None else []

    def close(self) -> None:
        if self._ledger is not None:
            self._ledger.close()

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
        if self._ledger is not None:
            return  # 持久历史的保留/清理必须显式进行，不能用内存TTL丢掉恢复索引。
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
