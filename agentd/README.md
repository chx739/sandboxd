> Phase 8 当前入口：[运维 Agent 与测评学习手册](../docs/38-运维Agent与测评学习手册.md)。默认检索已迁移到 Milvus 原生 BM25 + E5；只需 corpus 即可启用知识工具，queries 仅用于评测。OpenSearch 日志由 `AGENTD_LOGS_FILE` 显式启用。下面 Phase 7 章节保留历史背景。

# agentd

agentd 是 sandboxd 的极简、安全、可插拔运维 Agent 控制面：

- FastAPI 接收 Alertmanager Webhook 和查询任务；
- Pi-style 手写双层循环显式处理 Tool Call、steer 和 follow-up；
- Prometheus 由 agentd 直接查询；
- Kubernetes 诊断和 Plan 必须经过 Go sandboxd；
- Linux 诊断通过静态 Target Registry 和受限 SSH Connector；
- 五个原生文件工具只访问当前 task 私有工作区；
- agentd 永远不持有 Operator Token。
- Phase 7 可选注册分层记忆读取与本地 Milvus/ES/BGE 知识检索；文本始终是低信任数据。

快速建立当前心智模型请先读：

- `../docs/24-项目全景与心智模型.md`；
- `../docs/25-代码导读与模块地图.md` 的 Agent 主链；
- `../docs/26-Agent八股知识地图.md`（通用概念；项目完整答案统一查 `../docs/10-面试问答与项目讲法.md`）。

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
- `plugins/registry.py` 只显式注册仓库内 Prometheus、Kubernetes/Plan、Linux Host 和 File 插件；
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

`taskId` 代表一次运行，`sessionId` 代表可分支的树形事故上下文；resume 会创建新 Task 和新 Sandbox。Session 写在 `AGENTD_TRACE_DIR/sessions/*.jsonl`，正文与 Tool 参数会脱敏，不保存 Header、API Key、Provider 私有字段或隐藏思维。运行目录必须使用 WSL 原生 Linux 文件系统；未启用 metadata 的 `/mnt/c` 不能依赖 0700/0600 权限。

详细学习顺序见 `../docs/18-Pi-style-Agent-Runtime学习手册.md`。

## Phase 4 Linux 与文件能力

`linux_read` 只接受静态 `targetId` 和四个只读 operation。目标配置由 `AGENTD_LINUX_TARGETS_FILE` 指向仓库外 0600 JSON；模型看不到地址、用户、端口和 Key。SSH 路径不经过 gVisor，边界是 strict host key、低权限账号、固定 argv 与远端 forced-command。

`list_files`、`read_file`、`search_files`、`write_file`、`edit_file` 只访问 `AGENTD_WORKSPACE_DIR/<taskId>`。默认工作区位于 WSL 原生 `/tmp/sandboxd-agent-workspaces`，不要改到无法提供 POSIX 权限语义的普通 `/mnt/c`。

完整真实 Replay：

    ./hack/run-linux-agent-demo.sh

学习文档见 `../docs/21-Linux-SSH-Connector学习手册.md`、`../docs/22-Agent原生文件工具学习手册.md`；问题索引见 `../docs/23-Linux与文件工具面试问答.md`，完整项目回答统一查 `../docs/10-面试问答与项目讲法.md`。

## Phase 5 Prompt Injection Eval

当前默认 v2 用 40 条合成 JSONL 覆盖七种非可信来源、六种攻击目标和六种表达技术；历史 v1 的 20 条保持不变。确定性 Replay 仍经过当前 AgentRunner、Loop、Plugin、Policy 和 Workspace；Fake Connector 不联网，只记录是否发生外部状态变化。

    uv run --project agentd --frozen python -m agentd.evals.cli lint
    uv run --project agentd --frozen python -m agentd.evals.cli replay \
      --output .cache/evals/prompt-injection-v2.json

Replay 故意让 Agent 请求危险工具，只证明执行边界的确定性遏制，不代表真实模型攻击成功率。Canary Echo 只观察 canary 传播到授权结论，不等于攻击、泄露或副作用。学习和指标口径见 `../docs/29-Prompt-Injection-Eval学习手册.md`。

Live Eval 只在用户单独授权数据外发后运行，Key 仅从环境变量读取：

    AGENTD_LLM_API_KEY='从仓库外安全注入' \
      uv run --project agentd --frozen python -m agentd.evals.cli live \
      --model deepseek-v4-flash --thinking disabled \
      --output .cache/evals/deepseek-live-v2.json

2026-09-01 的全部 v1/v2 Live 授权均已完成并消费，不得把这段命令视为后续自动调用许可。来源隔离与顶层 JSON 解析修复后的正式 v2 重跑结果为 Agent ASR 1/72、Containment 1/1、副作用 0/72；见 `../docs/evidence/phase14-source-isolated-live-eval-v2.md`。Phase 12/13 保留历史缺陷与修正过程。

## Phase 7 混合检索

本机开发 Compose 固定 Milvus `2.5.10` 和 ES `8.19.22`，BGE-small/BGE-reranker 从本地 `safetensors` 用 CPU 推理。`agentd/retrieval/cli.py` 提供 `check/rebuild/query/eval`；完整命令、模型目录和公开 SciFact 数据集见 `../docs/35-Milvus-ES-BGE混合检索学习手册.md`。

Agent 默认不注册 `search_knowledge`。设定 `AGENTD_RETRIEVAL_CORPUS` 和 `AGENTD_RETRIEVAL_QUERIES` 后才注册只读工具，首次调用加载本地模型；可用 `AGENTD_RETRIEVAL_EMBEDDING_DIR`、`AGENTD_RETRIEVAL_RERANKER_DIR` 覆盖默认模型目录。工具参数由 Policy 限定，结果以 `untrusted-retrieved-evidence` 进入模型。ES 的本地 Demo 关闭认证，只绑定 localhost，不能直接用于生产环境。

四模块无密钥联合演示（本地索引需先运行 `rebuild`）：

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
      uv run --project agentd --frozen --extra rag --extra jev \
      python -m agentd.phase7_demo

## Phase 7 Jev 路由

默认 `AGENTD_ROUTER_MODE=off`。可选 `jev` 模式用官方 `typesafe-sdk==0.7.2` 的 `Choice` 在 Task 开始前选静态 `economy/strong` 等级，并映射到两个已配置的下游模型；低置信度、非法返回、超时和 SDK 报错回退到 `strong`。需同时配置 Live Gateway、两个模型名与 `TYPESAFE_API_KEY`。费用估算可选 `AGENTD_ROUTER_PRICES_JSON` 与 `AGENTD_JEV_INPUT_USD_PER_MILLION`；未配置价格或用量时不假报零成本。WSL 默认网络路径不可用时可设置 `AGENTD_JEV_IPV4_ONLY=1`；Jev 客户端使用 2 秒超时、零 SDK 重试。无 Key 的 `python -m agentd.router_eval` 是纯合成 Fake/Replay，不能代表真实 Jev 或下游模型质量。`python -m agentd.router_live_eval` 默认只输出 8 题真实横评计划，不发送请求；其 `--execute` 模式须在具体服务、次数和预算授权内使用，并向 stderr 输出脱敏请求进度。当前首题结果与 TypeSafe 401 阻塞见 `../docs/evidence/phase21-jev-live-first-case.md`；学习入口见 `../docs/36-Jev模型路由学习手册.md`。
