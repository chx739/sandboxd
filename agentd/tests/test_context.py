from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agentd.app.context import transform_model_context


class ContextTransformTest(unittest.TestCase):
    def test_keeps_system_and_complete_latest_tool_protocol(self) -> None:
        old_call = AIMessage(
            content="",
            tool_calls=[{"id": "old", "name": "read", "args": {}, "type": "tool_call"}],
        )
        latest_call = AIMessage(
            content="",
            tool_calls=[{"id": "latest", "name": "read", "args": {}, "type": "tool_call"}],
        )
        result = transform_model_context(
            [
                SystemMessage(content="security-rules"),
                HumanMessage(content="alert"),
                old_call,
                ToolMessage(content="x" * 200, tool_call_id="old"),
                latest_call,
                ToolMessage(content="latest-result", tool_call_id="latest"),
            ],
            budget=120,
        )

        self.assertEqual(result.messages[0].content, "security-rules")
        self.assertTrue(result.trimmed)
        ids = [
            message.tool_call_id
            for message in result.messages
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(ids, ["latest"])
        self.assertIs(result.messages[-2], latest_call)


if __name__ == "__main__":
    unittest.main()
