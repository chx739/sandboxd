# agentd

agentd 是 sandboxd 的极简、安全、可插拔运维 Agent 控制面：

- FastAPI 接收 Alertmanager Webhook 和查询任务；
- Pi-style 手写双层循环显式处理 Tool Call、steer 和 follow-up；
- Prometheus 由 agentd 直接查询；
- Kubernetes 诊断和 Plan 必须经过 Go sandboxd；
- agentd 永远不持有 Operator Token。

本地依赖使用已有用户态 uv：

    uv sync --project agentd --frozen
    uv run --project agentd python -m unittest discover -s agentd/tests
    uv run --project agentd uvicorn agentd.app.main:create_app --factory

默认 Replay，Live 模式需要显式设置 LLM Endpoint、Model 和 API Key。对于默认返回思维链且要求在 Tool Calling 轮次回传的 Provider，可设置 `AGENTD_LLM_THINKING=disabled`；默认值 `default` 不发送 Provider 私有参数。项目强制关闭 LangSmith tracing，不上传告警、工具结果或 Trace。

## Phase 2.1 内核边界

- ToolMessage 只接收 4 KiB 有界模型摘要，Trace 的 auditDetails 独立保存且最多 8 KiB；
- Trace 记录模型 usage/finishReason/耗时和完整生命周期事件；
- 每次模型调用前确定性裁剪旧轮次，System Prompt 与完整 Tool Call/Result 组不可拆；
- Task 取消后用独立清理 Task 释放已认领沙箱；
- 当时不做 Pi Session、steer、follow-up、长期记忆或工具并行；这是已经完成的 Phase 2.1 历史边界。

## Phase 3 Runtime

- `runtime/loop.py` 用内层 Tool/steer、外层 follow-up 的两个 `while` 展示 Pi 核心循环；
- `runner.py` 独立负责 Sandbox claim/release，取消后仍给清理 10 秒窗口；
- `plugins/registry.py` 只显式注册仓库内 Prometheus、Kubernetes/Plan 插件；
- Tool Schema 来自 Registry，但 Python Policy、sandboxd、RBAC 和审批门仍独立授权；
- 第一版工具保持顺序执行，不做动态插件、任意 Shell、Session 树或 TUI；
- `graph.py` 只是旧导入兼容层，项目已不再依赖 LangGraph。

运行控制与 Session API：

    POST /api/v1/tasks/{taskId}/steer
    POST /api/v1/tasks/{taskId}/follow-up
    POST /api/v1/tasks/{taskId}/cancel
    GET  /api/v1/sessions/{sessionId}
    POST /api/v1/sessions/{sessionId}/resume
    GET  /api/v1/plugins

`taskId` 代表一次运行，`sessionId` 代表可 resume 的线性事故上下文；resume 会创建新 Task 和新 Sandbox。Session 写在 `AGENTD_TRACE_DIR/sessions/*.jsonl`，正文与 Tool 参数会脱敏，不保存 Header、API Key、Provider 私有字段或隐藏思维。运行目录必须使用 WSL 原生 Linux 文件系统；未启用 metadata 的 `/mnt/c` 不能依赖 0700/0600 权限。

详细学习顺序见 `../docs/18-Pi-style-Agent-Runtime学习手册.md`。
