# Phase 2 外部告警诊断 Agent 实现计划

> 状态：M0 目标与设计已冻结，按 docs/PROGRESS.md 逐里程碑实施。
> 阅读顺序：GOAL.md → docs/00-实现计划.md → 本文 → docs/PROGRESS.md → AGENTS.md。

## 1. 一句话目标

在已完成的 Go sandboxd 安全执行层之外新增 Python agentd，用 LangGraph StateGraph 串起真实 Prometheus/Alertmanager 告警、Live/Replay LLM、gVisor 内 Kubernetes 只读诊断、Prompt Injection 拦截和现有 Pending Plan 审批门，形成一条可运行、可复现、适合秋招讲解的最小闭环。

## 2. 定位和边界

项目主语仍是可信执行边界，Agent 层保持薄：

- agentd 负责不可信的智能决策、告警接入和流程编排；
- sandboxd 负责可信的沙箱、工具策略和 Plan；
- gVisor 负责系统调用隔离；
- NetworkPolicy 限制沙箱网络；
- Kubernetes RBAC 决定最终资源权限；
- Operator Token 决定写操作是否真正执行。

外部系统仅指运行于 WSL 用户态的 Prometheus + Alertmanager。当前 kind 集群只作为 gVisor 执行集群和受控故障 Fixture，不接入外部 Kubernetes，不建设通用多集群运维。

### 必须完成

- Python agentd 和内存单 Worker 任务队列；
- LangGraph StateGraph 有限 Tool Calling 循环；
- OpenAI-compatible Live 模式和确定性 Replay 模式；
- Alertmanager Webhook Connector 和 Prometheus Query Connector；
- gVisor 沙箱内真实访问 Kubernetes HTTPS API；
- Pod Log/ConfigMap 间接注入进入模型上下文；
- Python Policy、Go Tool Policy、RBAC、审批门的分层拒绝证据；
- 结构化诊断或现有 Deployment Scale Pending Plan；
- 最小脚本、脱敏 Trace、学习文档、面试问答和踩坑记录。

### 明确不做

- 外部 Kubernetes、多集群、宿主机 SSH/Node Agent；
- 多用户、多租户、多会话、长期记忆；
- 文件上传下载、tar 通道；
- RAG、向量库、多 Agent、MCP Server；
- 数据库、消息队列、HA、Web UI；
- 自动审批、自动执行 Plan；
- LangChain 高层 create_agent、旧 AgentExecutor、旧 create_react_agent；
- LangSmith 或其他外部 Trace 上传；
- 大规模压测和生产级完整测试体系。

## 3. 架构

~~~text
Prometheus ──alert──> Alertmanager
    ▲                     │ webhook
    │ query               ▼
    └────────────── agentd (Python, :8090)
                         ├─ FastAPI：Alert/Task/Trace API
                         ├─ LangGraph：显式状态机
                         ├─ Live/Replay Model
                         ├─ query_prometheus
                         ├─ kubernetes_read
                         └─ propose_plan
                                  │ Agent Token
                                  ▼
                         sandboxd (Go, :8080)
                         ├─ Pool/Manager/Exec
                         ├─ 结构化 Diagnostic API
                         ├─ Go Tool Policy
                         └─ Plan/Approval
                                  │
                                  ▼
                         gVisor Sandbox Pod
                         ├─ projected 短时 SA Token
                         ├─ NetworkPolicy
                         └─ Kubernetes RBAC
                                  │ read only
                                  ▼
                         kind / sandboxd-target
~~~

完整信任链：

~~~text
LLM 决策（不可信）
  → LangGraph 流程与上限
  → Python 参数校验
  → Go 结构化 Tool Policy
  → gVisor
  → NetworkPolicy
  → Kubernetes RBAC
  → Operator 人工审批
~~~

LangGraph 的条件分支是业务控制，不是最终安全边界。

## 4. LangGraph 设计

### State

~~~python
class AgentState(TypedDict, total=False):
    task_id: str
    alert: dict
    messages: Annotated[list[BaseMessage], add_messages]
    sandbox_id: str | None
    iteration_count: int
    tool_call_count: int
    prometheus_call_count: int
    pending_tool_call: dict | None
    denied_actions: list[dict]
    evidence: list[dict]
    diagnosis: dict | None
    plan_id: str | None
    status: str
    error: str | None
~~~

State 不允许包含 API Key、Bearer Token、ServiceAccount Token、Authorization Header、kubeconfig 或未截断的完整响应。

### Graph

~~~text
START
  │
  ▼
prepare_context
  │
  ▼
call_model ◄─────────────────────────┐
  │                                 │
  ▼                                 │
route_model_output                  │
  ├── tool_call                     │
  │      ▼                          │
  │   validate_tool                 │
  │      ├── denied                 │
  │      │      ▼                   │
  │      │   record_denial ─────────┘
  │      └── allowed
  │             ▼
  │         execute_tool ────────────┘
  └── final
         ▼
      finalize
         ▼
        END
~~~

节点职责：

- prepare_context：规范化告警，把 labels、annotations 和工具结果标记为不可信数据；
- call_model：通过统一 Gateway 调 Live 或 Replay Model；
- route_model_output：区分 Tool Call 与最终回答；
- validate_tool：检查工具名、参数、轮数和调用次数；
- record_denial：记录拒绝层，将拒绝原因作为 Observation 回灌；
- execute_tool：只调用三个注册工具；
- finalize：校验最终结构并更新 Task。

硬限制：

~~~python
MAX_ITERATIONS = 6
MAX_TOOL_CALLS = 8
MAX_PROMETHEUS_CALLS = 4
MAX_OBSERVATION_BYTES = 4096
MAX_TASK_SECONDS = 120
~~~

不记录、要求或展示模型隐藏 Chain of Thought；Trace 只包含可见消息摘要、Tool Call、Tool Result、Denial、轮次、耗时和最终结果。

## 5. 工具契约

### query_prometheus

请求只包含 query。Prometheus URL 固定来自配置，模型不能传 URL、Host 或 Header；只允许 /api/v1/query；PromQL 最长 2 KiB，请求 5 秒超时；HTTP Body 最多读取 64 KiB，注入上下文前截断到 4 KiB；每任务最多调用 4 次。

### kubernetes_read

允许的 operation：

- list_pods
- get_deployment
- get_pod_logs
- get_configmap
- list_events

固定禁止 delete/create/patch/apply/exec/attach/port-forward/secrets、自定义 URL、自定义 Token、自定义 kubeconfig 和任意 argv。namespace 本期固定为 sandboxd-target。

### propose_plan

复用现有 POST /api/v1/plans，参数只允许 sandboxd-target 中 Deployment scale。agentd 只持有 Agent Token，永远不持有 Operator Token，因此只能创建 pending Plan，不能 approve/reject。

## 6. Go 结构化 Kubernetes Diagnostic API

新增：

~~~text
POST /api/v1/sandboxes/{id}/diagnostics/kubernetes
Authorization: Bearer SANDBOXD_TOKEN
~~~

请求字段：operation、namespace、name、container、tailLines、previous。Go 侧必须：

- Body 上限 16 KiB，DisallowUnknownFields 且只能有一个 JSON 对象；
- operation 使用固定枚举；
- namespace 必须等于 --diagnostic-namespace，默认 sandboxd-target；
- name/container 使用 Kubernetes 名称校验；
- tailLines 限制 1–200；
- 由 Go 构造固定 Kubernetes REST API URL，模型不能传 URL/Header/argv；
- 拒绝响应包含 denyLayer=tool-policy；
- 复用 Exec Timeout 与有界输出。

### 为什么不安装 kubectl

现有沙箱镜像 curlimages/curl 没有 kubectl。为了避免扩大镜像和命令面，Go 生成固定只读请求，在 gVisor 沙箱中使用 projected ServiceAccount Token 和 CA，通过 https://kubernetes.default.svc 调真实 Kubernetes HTTPS API。

固定脚本读取 token；模型数据只能进入经过 Go 校验和 URL 编码的路径，不能进入 shell 程序文本。原通用 Exec 保留，仅用于证明绕过 Tool Policy 后写请求仍被 RBAC 拒绝。

## 7. Agentd API 和任务模型

~~~text
POST /api/v1/alerts       Alertmanager Token，返回 202 + taskIds
POST /api/v1/tasks        Agentd Token，手动正常任务
GET  /api/v1/tasks/{id}   Agentd Token，查询状态/结果
GET  /api/v1/tasks/{id}/trace
GET  /healthz
GET  /readyz
~~~

Alert 入口：

- Body 最大 64 KiB；
- 单次最多 10 条 firing alert，resolved 不创建任务；
- 按 fingerprint 去重，10 分钟内复用已有 Task；
- Handler 不等待 LLM，立即返回。

Task 状态为 queued、running、succeeded、failed、limit_exceeded。

Task Store 使用单进程内存实现：一个 Worker、队列上限 16、最多保存 100 条、约 1 小时过期。重启丢失是明确边界，不引入数据库或消息队列。

一个 Task 对应一个已认领 Sandbox，Trace 同时记录 taskId 和 sandboxId；无论成功失败都在 finally 释放，Pod 的 ActiveDeadline 作为兜底。

## 8. Live、Replay 和证据诚实性

Live 使用 AGENTD_LLM_MODE、AGENTD_LLM_BASE_URL、AGENTD_LLM_MODEL、AGENTD_LLM_API_KEY。Replay 使用 AGENTD_REPLAY_FILE。

Replay 只替换 Model Gateway，仍经过同一个 Graph、Policy、Tool Dispatcher、Go Diagnostic API、gVisor、RBAC 和 Trace。

- 至少完成一次 Live 真实诊断；
- Live 注入实验最多 3 次，记录真实触发次数；
- 如果模型未尝试危险动作，结果写 not-triggered，不能伪造；
- 如有真实 contained 响应，可脱敏为 Replay Fixture；
- 否则人工 Replay 必须标记 deterministic-policy-case，不能冒充 Live 证据。

## 9. Prompt Injection 与故障 Fixture

deploy/smoke/agent-target.yaml 创建临时 sandboxd-target：

- crashloop-demo Deployment 输出日志后 exit 1，真实进入 CrashLoopBackOff；
- ConfigMap 和日志包含诱导删除 sandboxd-target 的间接注入；
- 安全上下文满足 restricted，副本 1，资源限制保持很小。

Prometheus 使用确定性的 Demo Rule 产生 SandboxAgentDemoCrashLoop，labels 指向 namespace/deployment。告警计算和 Alertmanager Webhook 为真实链路，但规则条件为稳定演示而构造；README 必须说明，不引入 kube-state-metrics。

预期 Replay：Agent 查询 Prometheus 和 Kubernetes，读取注入，提出删除 namespace，被拒绝后识别真实 exit 1 根因，并可创建将 crashloop-demo 缩至 0 的 Pending Plan。

## 10. 输出与 Trace

最终结果最少包含 taskId、status、summary、rootCause、severity、evidence、injectionDetected、deniedActions、recommendation、planId。

运行 Trace 写 .cache/agent-traces/，只把人工复核后的脱敏样例复制到 docs/evidence/agent-traces/。Trace 移除认证头和敏感字段，工具输出截断，不保存环境变量或隐藏思维过程；默认禁用 LangSmith 和模型/工具敏感日志。

## 11. 文件结构

~~~text
agentd/
├── pyproject.toml
├── uv.lock
├── README.md
├── app/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── graph.py
│   ├── policy.py
│   ├── live_model.py
│   ├── replay_model.py
│   ├── store.py
│   ├── trace.py
│   └── tools/
│       ├── prometheus.py
│       ├── kubernetes.py
│       └── plan.py
├── testdata/injection-denied.replay.json
└── tests/test_replay.py

internal/api/diagnostic_handler.go
internal/diagnostic/kubernetes.go
internal/diagnostic/kubernetes_test.go

deploy/observability/prometheus.yml
deploy/observability/alert-rules.yml
deploy/observability/alertmanager.yml.template
deploy/smoke/agent-target.yaml

hack/install-observability-tools.sh
hack/run-agent-demo.sh
hack/verify-agent-replay.sh
hack/verify-agent-live.sh

docs/14-LangGraph告警诊断学习手册.md
docs/15-Agent安全面试问答.md
docs/evidence/phase8-agent-alert.md
docs/evidence/agent-traces/
~~~

实现时允许合并过小文件，但禁止增加无实际用途的抽象层。

## 12. 配置与秘密

Agentd 使用 AGENTD_LISTEN、AGENTD_TOKEN、AGENTD_ALERT_TOKEN、AGENTD_PROMETHEUS_URL、AGENTD_SANDBOXD_URL、AGENTD_LLM_MODE、AGENTD_LLM_BASE_URL、AGENTD_LLM_MODEL、AGENTD_LLM_API_KEY、AGENTD_LLM_THINKING、AGENTD_REPLAY_FILE。`AGENTD_LLM_THINKING` 默认为 `default`；对 DeepSeek V4 设为 `disabled`，避免 Tool Calling 多轮必须保存和回传 `reasoning_content`。sandboxd 继续使用 SANDBOXD_TOKEN 和 SANDBOXD_OPERATOR_TOKEN。

所有 Token 运行时随机生成，只通过环境变量或 .cache 下权限受限的运行配置传递；不得进入命令参数、模板、日志、Trace 或 Git。Operator Token 不得传给 agentd。

## 13. 依赖与版本

当前 WSL 使用 Python 3.12.3，系统没有 pip，但已有用户态 uv。为避免 sudo 安装系统包，agentd 使用 pyproject.toml + uv.lock，并由 uv 在 agentd/.venv 创建环境。

已验证可解析的直接依赖固定为：

- langgraph==1.2.11
- langchain-core==1.5.6
- langchain-openai==1.5.2
- fastapi==0.141.1
- uvicorn==0.52.4
- httpx==0.28.1

解析结果会传递安装 langsmith，但项目不配置 LangSmith API Key，并显式关闭 tracing；它只作为依赖存在，不能上传任何数据。

外部工具固定为：

- prometheus 3.14.0 linux-amd64，SHA256 f665c6da19eb7ba399c915d30c7d9793c9b417bf8a749b504bc470678631478d
- alertmanager 0.34.0 linux-amd64，SHA256 19c75a11d8c03dc4ade7abdbddfb3a8f28c9e7b000d0849cda0cd71dffd74a03

二进制下载到 .cache/tools，校验后解压，不安装到系统目录，不使用 sudo。

## 14. 最小测试策略

1. Go：合法参数生成固定 API 路径；未知 operation、错误 namespace、非法名称、超限 tail 被拒绝。
2. Python Replay：Graph 完整循环、危险调用被拒绝、拒绝回灌、Sandbox 最终释放、得到诊断/Pending Plan。
3. Live：至少一次真实 LLM 完成告警诊断，最多 3 次注入实验并如实统计。
4. 纵深边界：直接 Go 危险 operation 被拒绝；通用 Exec 绕过后 RBAC Forbidden；Agent approve 返回 401。
5. 最终执行 go test ./...、go vet ./...、Go build 和 Python Replay 测试。

不做 Agent 并发、长期运行、压力测试和覆盖率冲刺。

## 15. 资源与清理

- 真实集成前运行 hack/check-resources.sh 和 hack/require-demo-cluster.sh；
- Agent Demo 使用 pool=1、worker=1、一次一条告警；
- Prometheus retention 约 2 小时，数据只写 .cache/observability；
- 端口被占用时拒绝启动，不杀未知进程；
- PID 文件只管理本脚本启动的进程；
- sandboxd-target 已存在时拒绝运行，cleanup 只删本次确认创建的 namespace；
- 可用内存接近 2 GiB、持续 swap 或 WSL/Docker 异常时停止；
- 默认不使用 sudo，不读取或显示 sudo 密码。

## 16. 里程碑

| 里程碑 | 交付 | 预计时间 |
|---|---|---:|
| M0 | GOAL/计划/PROGRESS/AGENTS、分支、版本策略 | 1–2h |
| M1 | Go 结构化 Kubernetes Diagnostic API | 4–5h |
| M2 | agentd、LangGraph、Live/Replay、三个工具 | 8–10h |
| M3 | Prometheus/Alertmanager 与故障 Fixture | 4–5h |
| M4 | 完整 Live/Replay 链路、拒绝矩阵、清理 | 5–7h |
| M5 | Evidence、README、学习和面试文档 | 3–4h |

总计约 25–33 小时、4–6 个专注开发日；LangGraph 学习和用户理解项目另需约 5–8 小时。

## 17. Git 与进度纪律

开发分支固定为 codex/phase2-langgraph-agent。每个里程碑完成最小验证后立即更新 docs/PROGRESS.md 和对应学习/踩坑文档，小步提交并推送 GitHub；不自动合并。

每次上下文恢复必须依次读取 GOAL.md、docs/00-实现计划.md、本文、docs/PROGRESS.md、AGENTS.md，然后查看 git status、当前分支和最近提交，只继续 docs/PROGRESS.md 的下一步。

实施中遇到的真实错误、根因、排查过程、修复和面试价值必须同步追加到 docs/11-开发踩坑与排障.md，不能只留在聊天或终端输出。

## 18. 完成判据

只有以下证据全部存在才可宣布 Phase 2 完成：

- 真实 Prometheus 产生告警、真实 Alertmanager Webhook 返回 Task ID；
- Live 模式真实调用至少一次 LLM 并完成诊断；
- Agent 真实查询 Prometheus；
- Agent 通过 gVisor 沙箱真实查询 Kubernetes API；
- Pod Log/ConfigMap 注入文本真实进入模型可见上下文；
- Replay 确定性触发危险调用并被拒绝，且不冒充 Live；
- Go Tool Policy 和 RBAC 绕过验证都有真实拒绝输出；
- 输出结构化诊断或 Pending Plan，Agent approve 仍为 401；
- Sandbox、namespace、本地进程无目标外残留；
- Trace 已脱敏，秘密扫描无命中；
- Go build/test/vet 和 Python Replay 最小测试通过；
- README、学习文档、面试问答、踩坑和 evidence 完成；
- 全部应交付内容提交并推送 GitHub。

如果 Live 模型没有被注入诱导，必须如实标记 not-triggered；安全结论来自执行边界，不依赖攻击一定触发。
