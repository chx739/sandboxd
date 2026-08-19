"""sandboxd Phase 2 的最小 Agent 服务。"""

import os

# LangChain Core 会传递安装 langsmith，但本项目的集群证据不能被自动上传。
# 在任何 LangChain/LangGraph 模块导入前显式关闭两代 tracing 开关。
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
