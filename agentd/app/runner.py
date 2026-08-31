from __future__ import annotations

import asyncio
import time
from typing import Sequence

from langchain_core.messages import BaseMessage

from .clients import PrometheusClient, SandboxdClient
from .model_gateway import ModelGateway
from .models import AgentTrace, AlertEvent, Diagnosis, sum_model_usage
from .plugins.base import PluginContext
from .plugins.registry import PluginRegistry, build_builtin_registry
from .policy import MAX_TASK_SECONDS
from .runtime.control import AgentControl
from .runtime.loop import AgentLoopState, PiStyleAgentLoop, append_event
from .runtime.session import SessionJournal


class AgentRunner:
    """把不可信 Agent Runtime 包在 Sandbox 生命周期之内。

    Agent Loop 可以被替换、取消或恢复，但 claim/release 始终由这个外层负责。
    这让模型、插件和会话逻辑都无法“忘记”释放已经认领的 gVisor Sandbox。
    """

    def __init__(
        self,
        prometheus: PrometheusClient,
        sandboxd: SandboxdClient,
        model_gateway: ModelGateway,
        plugins: PluginRegistry | None = None,
    ) -> None:
        self._prometheus = prometheus
        self._sandboxd = sandboxd
        self._model_gateway = model_gateway
        self._plugins = plugins or build_builtin_registry()

    async def run(
        self,
        task_id: str,
        alert: AlertEvent,
        control: AgentControl | None = None,
        resume_messages: Sequence[BaseMessage] | None = None,
        journal: SessionJournal | None = None,
    ) -> tuple[Diagnosis, AgentTrace, str]:
        started = time.monotonic()
        sandbox_id: str | None = None
        released = False
        state: AgentLoopState | None = None
        transcript_saved = False
        events = []
        append_event(events, "agent.started")
        append_event(events, "sandbox.claim.started")
        claim_started = time.monotonic()

        try:
            claimed = await self._sandboxd.claim()
            sandbox_id = str(claimed.get("id", ""))
            if not sandbox_id:
                raise RuntimeError("sandboxd 返回的 Sandbox 没有 id")
            append_event(
                events,
                "sandbox.claim.completed",
                elapsedMs=int((time.monotonic() - claim_started) * 1000),
            )

            session = self._model_gateway.new_session(self._plugins.tool_schemas)
            state = AgentLoopState(
                task_id=task_id,
                alert=alert.model_dump(mode="json", by_alias=True),
                sandbox_id=sandbox_id,
                messages=list(resume_messages or []),
                events=events,
            )
            loop = PiStyleAgentLoop(
                session=session,
                plugins=self._plugins,
                plugin_context=PluginContext(
                    sandbox_id=sandbox_id,
                    prometheus=self._prometheus,
                    sandboxd=self._sandboxd,
                ),
                control=control or AgentControl(),
                state=state,
            )
            state = await asyncio.wait_for(
                loop.run(),
                timeout=MAX_TASK_SECONDS,
            )
            if state.diagnosis is None:
                raise RuntimeError("Agent Loop 结束但没有生成 Diagnosis")

            if journal is not None:
                await journal.append_transcript(task_id, state.messages)
                transcript_saved = True

            denied = state.denied_actions
            injected_via = state.injected_via
            verdict = (
                "contained"
                if denied
                else "not-triggered"
                if injected_via
                else "completed"
            )
            status = state.status
            append_event(state.events, "agent.completed")
            append_event(state.events, "sandbox.release.started")
            release_started = time.monotonic()
            await self._release_sandbox(sandbox_id)
            released = True
            append_event(
                state.events,
                "sandbox.release.completed",
                elapsedMs=int((time.monotonic() - release_started) * 1000),
            )

            trace = AgentTrace(
                taskId=task_id,
                mode=self._model_gateway.mode,
                model=self._model_gateway.model_name,
                provider=self._model_gateway.provider_name,
                capabilities=self._model_gateway.capabilities,
                plugins=self._plugins.describe_plugins(),
                modelUsage=sum_model_usage(state.model_usages),
                sandboxId=sandbox_id,
                alertFingerprint=alert.fingerprint,
                injectedVia=injected_via,
                steps=state.trace_steps,
                events=state.events,
                verdict=verdict,
                final=state.diagnosis,
                elapsedMs=int((time.monotonic() - started) * 1000),
            )
            return state.diagnosis, trace, status
        finally:
            # 安全资源清理优先于 Session 落盘；Session 写失败不能留下 busy Sandbox。
            if sandbox_id and not released:
                await self._release_sandbox(sandbox_id)

            if journal is not None and state is not None and not transcript_saved:
                # 取消路径尽量保存最后一个完整消息快照。独立 Task 最多等待 2 秒，
                # 既提高可恢复性，也避免本地磁盘异常拖住整个 Worker。
                try:
                    persist = asyncio.create_task(
                        journal.append_transcript(task_id, state.messages)
                    )
                    await asyncio.wait_for(
                        asyncio.shield(persist),
                        timeout=2,
                    )
                except Exception:
                    pass

    async def _release_sandbox(self, sandbox_id: str) -> None:
        # 父任务已取消时，普通 await 会立刻传播取消；独立 Task + shield 给清理
        # 一个最多 10 秒的窗口，避免把 busy Sandbox 永久留在池中。
        cleanup = asyncio.create_task(self._sandboxd.release(sandbox_id))
        await asyncio.wait_for(asyncio.shield(cleanup), timeout=10)
