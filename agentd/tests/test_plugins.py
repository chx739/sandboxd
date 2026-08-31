from __future__ import annotations

import unittest

from agentd.app.plugins import build_builtin_registry


class PluginRegistryTest(unittest.TestCase):
    def test_builtin_registry_exposes_only_expected_tools(self) -> None:
        registry = build_builtin_registry()

        names = [
            schema["function"]["name"]
            for schema in registry.tool_schemas
        ]
        self.assertEqual(
            names,
            ["query_prometheus", "kubernetes_read", "propose_plan"],
        )
        self.assertEqual(
            [manifest.plugin_id for manifest in registry.manifests],
            ["prometheus", "kubernetes"],
        )

    def test_tool_schemas_are_defensive_copies(self) -> None:
        registry = build_builtin_registry()
        first = registry.tool_schemas
        first[0]["function"]["name"] = "tampered"

        # Provider 或测试代码修改返回对象时，不能污染下一次任务的工具注册表。
        self.assertEqual(
            registry.tool_schemas[0]["function"]["name"],
            "query_prometheus",
        )

    def test_unknown_tool_does_not_resolve(self) -> None:
        registry = build_builtin_registry()
        self.assertIsNone(registry.resolve("run_any_shell"))


if __name__ == "__main__":
    unittest.main()
