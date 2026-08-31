from __future__ import annotations

import asyncio
import unittest
from typing import Sequence

from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import AIMessage

from agentd.app.model_gateway import ModelInvocation
from agentd.app.models import ModelUsage
from agentd.app.plugins import build_builtin_registry
from agentd.app.plugins.base import PluginContext
from agentd.app.runtime import AgentControl, AgentLoopState, PiStyleAgentLoop


def _diagnosis(summary: str) -> AIMessage:
    return AIMessage(
        content=(
            '{"summary":"%s","rootCause":"","severity":"warning",'
            '"recommendation":"observe"}' % summary
        )
    )


class SequenceSession:
    def __init__(
        self,
        responses: list[AIMessage],
        block_first: bool = False,
    ) -> None:
        self._responses = responses
        self._block_first = block_first
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[list[BaseMessage]] = []

    async def invoke(
        self,
        messages: Sequence[BaseMessage],
    ) -> ModelInvocation:
        self.calls.append(list(messages))
        if len(self.calls) == 1 and self._block_first:
            self.started.set()
            await self.release.wait()
        response = self._responses[len(self.calls) - 1]
        return ModelInvocation(
            message=response,
            usage=ModelUsage(),
            finish_reason="stop",
            elapsed_ms=0,
        )


def _loop(
    session: SequenceSession,
    control: AgentControl,
) -> PiStyleAgentLoop:
    # 本测试不执行工具，因此窄 Client 用 object 占位即可；类型只服务静态检查。
    context = PluginContext(
        sandbox_id="sandbox-test",
        prometheus=object(),  # type: ignore[arg-type]
        sandboxd=object(),  # type: ignore[arg-type]
    )
    return PiStyleAgentLoop(
        session=session,
        plugins=build_builtin_registry(),
        plugin_context=context,
        control=control,
        state=AgentLoopState(
            task_id="task-loop",
            alert={"status": "firing"},
            sandbox_id="sandbox-test",
        ),
    )


class PiStyleLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_steer_is_applied_after_current_turn(self) -> None:
        control = AgentControl()
        session = SequenceSession(
            [_diagnosis("first"), _diagnosis("after-steer")],
            block_first=True,
        )
        loop = _loop(session, control)

        task = asyncio.create_task(loop.run())
        await session.started.wait()
        control.steer("只读，不要创建 Plan")
        session.release.set()
        state = await task

        self.assertEqual(len(session.calls), 2)
        second_human_messages = [
            str(message.content)
            for message in session.calls[1]
            if isinstance(message, HumanMessage)
        ]
        self.assertIn("只读，不要创建 Plan", second_human_messages)
        self.assertEqual(state.diagnosis.summary, "after-steer")
        self.assertIn("steer.applied", [event.type for event in state.events])

    async def test_follow_up_runs_only_after_natural_stop(self) -> None:
        control = AgentControl()
        control.follow_up("补充一条最终建议")
        session = SequenceSession(
            [_diagnosis("first"), _diagnosis("after-follow-up")]
        )

        state = await _loop(session, control).run()

        self.assertEqual(len(session.calls), 2)
        second_human_messages = [
            str(message.content)
            for message in session.calls[1]
            if isinstance(message, HumanMessage)
        ]
        self.assertIn("补充一条最终建议", second_human_messages)
        self.assertEqual(state.diagnosis.summary, "after-follow-up")
        self.assertIn(
            "follow-up.applied",
            [event.type for event in state.events],
        )


if __name__ == "__main__":
    unittest.main()
