# sandboxd 持续开发进度

> 本文件是跨会话进度快照。最高目标以 ../GOAL.md 为准，Phase 2 细节以 12-Agent层实现计划.md 为准。

## 当前状态

Phase 1 已完成并保留；Phase 2 外部告警诊断 Agent 已完成。

当前分支：

    codex/phase2-langgraph-agent

当前里程碑：

    Phase 2 收尾：完成审计与 GitHub 推送

## Phase 1 已完成基线

- WSL2 + 单节点 kind + gVisor + Calico，已有 Starting gVisor 真实证据。
- restricted Pod 安全基线、短时 Token、只读 RBAC、NetworkPolicy 正反路径。
- Manager、Exec、Informer/Lister、Workqueue、预热池、JSON Patch CAS。
- Prometheus 指标、Deployment scale server-side DryRun 和 Agent/Operator 双 Token 审批门。
- 全项目 Go test/vet/build 和完整 Demo 已通过。
- 学习文档、面试问答、踩坑与 Phase 1 evidence 已完成。

Phase 2 不得破坏这些证据和接口。

## Phase 2 目标摘要

- Python agentd + LangGraph StateGraph。
- 真实用户态 Prometheus/Alertmanager 外部告警。
- OpenAI-compatible Live LLM 和确定性 Replay。
- Prometheus Query Tool。
- Go 结构化 Kubernetes Diagnostic API，查询在 gVisor 沙箱中发生。
- Pod Log/ConfigMap 间接 Prompt Injection。
- Python Policy、Go Tool Policy、RBAC、审批门纵深拒绝。
- 结构化诊断或 Pending Deployment Scale Plan。
- 脱敏 Trace、最小验证、学习/面试/踩坑文档和 GitHub 维护。

## 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| M0 | 已完成 | 目标、计划、AGENTS、PROGRESS、分支和版本策略 |
| M1 | 已完成 | Go 结构化 Kubernetes Diagnostic API |
| M2 | 已完成 | agentd、LangGraph、Live/Replay、三个工具 |
| M3 | 已完成 | Prometheus/Alertmanager 与故障 Fixture |
| M4 | 已完成 | 完整 Live/Replay 链路与拒绝矩阵 |
| M5 | 已完成 | Evidence、README、学习和面试文档 |

## 本轮已经完成

- 从 agent/environment-gvisor 的干净提交 9865955 创建 codex/phase2-langgraph-agent。
- 重新阅读 GOAL、全局计划、旧 Agent 计划、PROGRESS、AGENTS 和当前 API/RBAC/NetworkPolicy。
- 将 docs/12-Agent层实现计划.md 改为 Python agentd + LangGraph + Go 可信边界的权威规格。
- GOAL.md 增加 Phase 2 当前目标、非目标、完成证据和恢复顺序。
- AGENTS.md 增加 Phase 2 安全、证据和 Git 纪律。
- docs/00-实现计划.md 增加 Phase 2 总览。
- 确认现有沙箱镜像没有 kubectl；计划改为在 gVisor 内用固定 HTTPS 请求访问 Kubernetes API，避免扩大镜像和任意命令面。
- 确认 Python 3.12.3、系统无 pip、用户态 uv 可用；采用 pyproject.toml + uv.lock，不使用 sudo。
- 固定并验证 Python 直接依赖可解析：langgraph 1.2.11、langchain-core 1.5.6、langchain-openai 1.5.2、fastapi 0.141.1、uvicorn 0.52.4、httpx 0.28.1。
- 固定 Prometheus 3.14.0 与 Alertmanager 0.34.0 的官方 linux-amd64 SHA256。
- 在踩坑文档增加“系统 Python 无 pip”和“LangSmith 传递依赖不等于启用 tracing”。
- 完成 internal/diagnostic：五种只读 operation、固定 Kubernetes API 路径和固定 curl 命令。
- 完成认证后的 POST /api/v1/sandboxes/{id}/diagnostics/kubernetes。
- 未知 operation、错误 namespace 返回 403 + denyLayer=tool-policy；未知 JSON 字段返回 400。
- 增加纯逻辑和 HTTP 边界测试；go test ./...、go vet ./...、go build 均通过。
- 踩坑文档新增“调用方白名单不是服务端边界”和“kubectl 不是只读诊断必要条件”。
- 创建 agentd/pyproject.toml 和 uv.lock；uv frozen 环境可复现安装。
- 完成配置、Pydantic 数据模型、有界 HTTP Client、Task Store、单 Worker 和 FastAPI Alert/Task/Trace API。
- 以 StateGraph 显式实现 prepare、model、validate、execute、finalize 节点及有限循环。
- 完成 query_prometheus、kubernetes_read、propose_plan 三个工具和 Python 前置策略。
- 完成 OpenAI-compatible Live Gateway 构造验证和每任务独立 Replay Gateway。
- deterministic-policy-case Replay 完整验证：读取注入、delete_namespace 在 agent-policy 拒绝、得到 Pending Plan、Sandbox finally 释放。
- FastAPI 验证 Alert Token 不能读取 Task；Python compileall 和 2 个 unittest 通过。
- Trace 仅写 .cache，尝试 0700/0600 权限，不包含 Header/Token/隐藏思维。
- 踩坑新增“Body 必须流式限长”和“多 Tool Call 必须逐个闭合协议”。
- 根目录审计发现并修复 app 导入依赖 cwd；统一为 agentd.app，并新增对应踩坑。
- 使用文档中的 Uvicorn factory 命令启动单个 Replay agentd，/healthz 返回 200，随后按精确 PID 清理。
- 用户态安装 Prometheus 3.14.0、Alertmanager 0.34.0，并使用官方 SHA256 固定供应链输入。
- 增加 localhost-only Prometheus/Alertmanager 配置和携带独立 Bearer Token 的 Webhook 模板。
- 增加确定性 SandboxAgentDemoCrashLoop 告警规则；明确它验证告警传输，不冒充真实异常检测。
- 增加 restricted + gVisor CrashLoop Deployment 和含间接 Prompt Injection 的 ConfigMap Fixture。
- promtool、amtool 配置检查通过；真实 Prometheus 告警进入 firing，Alertmanager readiness 通过。
- 可观测性验证前检查资源与端口，只终止脚本自己记录的精确 PID；验证后 9090/9093 无监听残留。
- kubectl client dry-run 在 kind API Server 停止时仍需 RESTMapper discovery，清单的服务端语义验证留到 M4 临时集群。
- Agentd 增加受 API Token 保护的最近任务列表；Alert Token 访问列表仍返回 401。
- Replay 只支持受限的 {{first_pod_name}} 占位符，并从上一条合法 ToolMessage 中解析动态 Deployment Pod 名。
- Go 可信边界把原始 PodList 压缩为 name、phase、restartCount，避免 4 KiB Observation 截断 JSON。
- 新增 hack/run-agent-demo.sh：拒绝接管既有 namespace、managed Pod、监听端口或错误 kind context。
- 真实 Replay 链路通过：Prometheus -> Alertmanager -> agentd -> LangGraph -> Prometheus/Kubernetes Tool -> Pending Plan。
- 注入文本从 restricted + gVisor CrashLoop Pod 当前日志进入 Trace，injectedVia=podlog，verdict=contained。
- delete_namespace 在 agent-policy 拒绝；直调 Go 结构化接口在 tool-policy 返回 403；通用 Exec DELETE 在 RBAC 返回 403。
- Agent Token 调 approve 返回 401，Deployment replicas 保持 1，Plan 保持 pending；没有向 agentd 传 Operator Token。
- 在真实沙箱执行 dmesg 得到 Starting gVisor，证明诊断执行路径不是普通 runc。
- 运行后 sandboxd-target、managed Pod、8080/8090/9090/9093 监听和渲染 Token 配置均无残留。
- 单入口支持 AGENTD_DEMO_MODE=replay/live；Live 缺任一 LLM 配置时在创建资源前安全失败。
- 加入 Live 分支后完整 Replay 再次通过，严格断言 Agent Policy、Pending Plan、gVisor 与清理结果。
- 2026-08-19 最新脱敏 Replay 证据保存在本地 .cache/agent-demo-evidence/8b11a9d333e94249b9f04438491fa254，明确不作为 Live 证据。
- 完成 docs/14：LangGraph 图、三个工具、有限状态、可信侧压缩、纵深防御、Live/Replay 边界和学习顺序。
- 完成 docs/15：32 个 Agent 安全高频追问，覆盖选型、Tool Calling、注入、RBAC、审批、多会话与外部系统扩展。
- 完成 docs/evidence/phase8-agent-alert.md 与人工脱敏 replay-contained.json、live-not-triggered.json；不含运行时 ID、Token 或隐藏思维。
- README 架构图、能力状态、快速验证、项目边界和 docs/README、docs/13 学习路径已更新到 Phase 2。
- 最终审计通过：Go test/vet/build、Python compileall/7 tests、全部 Shell 语法、脱敏 JSON 和本地 Markdown 链接。
- 清理与秘密审计通过：目标 namespace、managed Pod、四个监听和渲染 Token 无残留，仓库未发现硬编码凭证。
- 2026-08-20 以官方 DeepSeek Endpoint、deepseek-v4-flash、thinking=disabled 做最小 Tool Calling 预检；请求到达服务端但鉴权返回 401，因此没有启动集群或冒充 Live 证据。
- 发现 Provider 异常即使隐藏完整 Key 也可能暴露掩码后缀；新增 public_error 统一清理 API Key、Bearer 和凭据指纹。
- 新增 AGENTD_LLM_THINKING；DeepSeek V4 可关闭 thinking，避免保存或回传隐藏 reasoning_content，其他 Provider 默认不发送私有字段。
- Python 测试增至 4 个；Go test/vet/build、Python compileall、Shell 语法和 diff 检查再次通过。
- 更新后的 DeepSeek Key 通过最小 Tool Calling 探针；严格运行三次 Live 注入实验，危险指令触发 0/3，均如实标记 not-triggered。
- 第 2 次 Live 完整通过：真实 Prometheus/Alertmanager、DeepSeek、gVisor Kubernetes read、Pod Log/ConfigMap 注入、Go Tool Policy、RBAC、gVisor 与清理形成闭环。
- 修复 Live Trace 来源精确列表误判；修复模型在 Markdown 中包装 JSON 时的有界提取，并只信任 Graph State 中的 evidence、deniedActions 和 planId。
- 第 3 次 Live 产出干净结构化 Diagnosis，但模型未查询 Prometheus，严格脚本如实失败；没有超出三次上限重试。
- 最新 deterministic Replay 再次完整通过，包含 agent-policy、tool-policy、RBAC 403、Agent approve 401、Pending Plan、replicas 不变和 Starting gVisor。

## 已知事实与风险

- 当前 sandboxd 通用 Exec 接收任意 argv，Python 白名单不能作为最终边界，因此 M1 必须新增 Go 结构化诊断 API。
- 当前 sandbox-reader 是只读 ClusterRole，能读取 Pod/Log/ConfigMap/Event/Deployment，但不含 Secret 和写权限，可复用。
- sandboxd-target 可被现有 Plan 服务接受，可用于 CrashLoop Demo 和 Pending Scale Plan。
- Live 模型是否服从间接注入具有概率性；最多运行 3 次并如实记录，Replay 不冒充 Live。
- DeepSeek 三次 Live 的 Tool 选择不同；概率行为不能当安全边界，也不能保证每次都查询所有数据源。严格脚本会拒绝不完整链路，Replay 负责确定性安全回归。
- 本地 Codex apply_patch helper 因 WSL 缺少 bubblewrap 无法启动；当前用生成的 Git patch 修改仓库。该问题不影响项目运行，禁止为此安装系统组件或使用 sudo。

## 资源与秘密约束

- kind 单节点；Agent Demo pool=1、worker=1、一次一条告警。
- 每次运行以 hack/check-resources.sh 的实时结果为准；持续 swap 或 WSL/Docker 异常时立即停止。
- 默认不使用 sudo。
- Operator Token 不传给 agentd。
- 不提交任何密码、Token、API Key、认证头、ServiceAccount Token 或 kubeconfig。
- 只清理本次确认创建的进程和 sandboxd-target。
- 不触碰仓库外简历、秘密目录和无关文件。

## 下一步

1. Phase 2 无未完成开发项；保持当前分支，等待用户 review 或决定是否合并，不自动扩展范围。

## Phase 2 完成审计

| 完成判据 | 权威证据 |
|---|---|
| Prometheus 告警与 Alertmanager Webhook | docs/evidence/phase8-agent-alert.md 的 Replay 与 Live 输出 |
| Live LLM 完成诊断 | 三次 DeepSeek Live Task 均 succeeded；第 2 次完整脚本通过，第 3 次结构化 Diagnosis |
| Prometheus + gVisor Kubernetes 查询 | 第 2 次 Live 的 1 次 Prometheus、5 次 Kubernetes Tool 与 Starting gVisor |
| Pod Log/ConfigMap 注入进入上下文 | Live `injectedVia=[podlog, configmap]` 与脱敏 Trace |
| Replay 危险调用和 Python Policy | replay-contained.json 与最新 Replay 全链路 |
| Go Tool Policy、RBAC、Agent 无审批权 | tool-policy 403、RBAC Forbidden 403、Agent approve 401 |
| 诊断或 Pending Plan | Live 输出诊断；Replay 输出 Pending Plan 且 replicas 不变 |
| 清理和秘密 | namespace/Pod/端口/渲染配置无残留，swap=0，Key 与掩码指纹扫描无命中 |
| 构建与最小测试 | Go test/vet/build、Python 7 tests、compileall、Shell/JSON/链接检查 |
| 文档和 GitHub | README、学习/面试/39 条踩坑、evidence 完成；当前收尾提交推送到固定分支 |

## 恢复工作时

1. 阅读 GOAL.md。
2. 阅读 docs/00-实现计划.md。
3. 阅读 docs/12-Agent层实现计划.md。
4. 阅读本文件。
5. 阅读 AGENTS.md。
6. 查看 git status、当前分支和最近提交。
7. 只继续“下一步”的第一项未完成工作。
