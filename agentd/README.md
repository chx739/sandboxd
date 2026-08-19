# agentd

agentd 是 sandboxd Phase 2 的薄 Agent 控制面：

- FastAPI 接收 Alertmanager Webhook 和查询任务；
- LangGraph StateGraph 显式编排模型、工具、拒绝和结束分支；
- Prometheus 由 agentd 直接查询；
- Kubernetes 诊断和 Plan 必须经过 Go sandboxd；
- agentd 永远不持有 Operator Token。

本地依赖使用已有用户态 uv：

    uv sync --project agentd --frozen
    uv run --project agentd python -m unittest discover -s agentd/tests
    uv run --project agentd uvicorn agentd.app.main:create_app --factory

默认 Replay，Live 模式需要显式设置 LLM Endpoint、Model 和 API Key。项目强制关闭 LangSmith tracing，不上传告警、工具结果或 Trace。
