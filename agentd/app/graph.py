"""Phase 2 导入兼容层。

真实实现已经迁移到 runner.py 和 runtime/loop.py。保留这个小文件，使旧测试、
旧文档示例和可能的外部导入不会因为 Phase 3 重构立即失效。
"""

from .runner import AgentRunner
from .runtime.loop import parse_final_diagnosis

__all__ = ["AgentRunner", "parse_final_diagnosis"]
