# sandboxd 持续开发进度

> 本文件是跨会话进度快照。最高目标以 ../GOAL.md 为准；当前学习与项目口径以 README.md、docs/README.md 和 24-项目全景与心智模型.md 为准。

## 当前状态

Phase 1–4 已完成并保留，稳定实现已快进合并并推送 GitHub main。项目功能暂时冻结，当前只做文档、学习和秋招面试收口。

当前分支：

    main

当前里程碑：

    Phase 1–4 全部完成；文档体系已按当前真相重构

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

## Phase 2.1 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| P0 | 已完成 | 目标、计划、AGENTS、PROGRESS 与数据模型冻结 |
| P1 | 已完成 | ToolResult 双通道 |
| P2 | 已完成 | 生命周期事件、模型 usage/finishReason |
| P3 | 已完成 | 上下文预算、取消与独立清理 |
| P4 | 已完成 | 最小验证、学习/面试/踩坑、README、GitHub |

## Phase 3 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| M0 | 已完成 | GOAL、AGENTS、计划、PROGRESS、分支和 9 个 Python 测试基线 |
| M1 | 已完成 | 受信任插件接口、注册表、Prometheus/Kubernetes 插件 |
| M2 | 已完成 | 手写双层 Agent Loop 替换 StateGraph |
| M3 | 已完成 | 线性 Session-lite、steer/follow-up/cancel/resume API |
| M4 | 已完成 | 最小验证、Demo、学习/面试/踩坑、README、GitHub |

## Phase 4 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| M0 | 已完成 | GOAL、AGENTS、计划、PROGRESS、分支和资源审计 |
| M1 | 已完成 | 改造前低资源真实 Replay E2E |
| M2 | 已完成 | 受限 SSH LinuxHostPlugin 与一次性 Demo Target |
| M3 | 已完成 | list/read/search/write/edit task 工作区工具 |
| M4 | 已完成 | Policy、Runtime、Session、Trace、Replay 接线与首次改造后 E2E |
| M5 | 已完成 | 学习/面试/踩坑、最终验证、GitHub |

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
- 用户明确允许后，以 sudo 从 Ubuntu 软件源只安装 `bubblewrap 0.9.0-1ubuntu0.1`，恢复受保护的终端/补丁命令；未修改其他系统配置。

## 资源与秘密约束

- kind 单节点；Agent Demo pool=1、worker=1、一次一条告警。
- 每次运行以 hack/check-resources.sh 的实时结果为准；持续 swap 或 WSL/Docker 异常时立即停止。
- 默认不使用 sudo。
- Operator Token 不传给 agentd。
- 不提交任何密码、Token、API Key、认证头、ServiceAccount Token 或 kubeconfig。
- 只清理本次确认创建的进程和 sandboxd-target。
- 不触碰仓库外简历、秘密目录和无关文件。

## 下一步

1. 功能没有未完成项；优先按 docs/13 的路线学习、演示和准备秋招面试。
2. 后续维护先读 docs/README、docs/24 和 docs/25，历史 Phase 计划只用于追溯。
3. 未获新授权前，不增加任意 SSH/Bash、远端写入、动态插件或生产级功能。

## 文档收口记录

- Phase 4 完整历史已快进合并到 main，并推送 GitHub；没有 merge commit 和冲突。
- 文档从“按 Phase 开发顺序”改为“当前真相、模块学习、面试表达、历史证据”四层导航。
- 新增项目全景与心智模型、代码导读与模块地图、Agent 八股知识地图、简历与面试表达手册。
- 重写项目学习路径和综合面试题库，当前口径统一为手写 Pi-style 双层循环和九个结构化工具。
- Phase 2 LangGraph、Phase 2.1、Phase 3、Phase 4 计划保留为历史资料，不删除真实证据。
- 文档收口轻量验证通过：Go test -p 1、go vet、go build、Python frozen 22 tests、全部 Shell 语法、JSON、Markdown 本地链接、diff check 和新增内容秘密模式扫描。
- 本轮没有启动 kind、Docker、Prometheus、Alertmanager、SSH Target 或 Live LLM；只改文档与项目描述元信息，不修改运行时代码。

## 2026-09-01 main 全量回归

- 在提交 2c47579 上串行执行完整静态检查与 Phase 1–4 E2E；开始时 16 CPU、可用内存 7097 MiB、swap 0。
- 经 kind 标签、角色和固定镜像摘要核实后，只启动已停止的项目 sandboxd-control-plane；未触碰停止的 minikube 和 minio-tutorial。
- Go test -p 1/vet/build、Python compileall/22 tests、Shell、JSON、Markdown 链接、diff 和秘密模式检查全部通过。
- Phase 1 demo 全部通过：gVisor、RBAC/NetworkPolicy、Manager/Exec、5 并发唯一认领、6 次 CAS 冲突、Metrics、DryRun、Agent/Operator 分权和 stale Plan。
- Agent Replay 全链通过：Prometheus/Alertmanager、Prometheus Query、gVisor Kubernetes read、Pod Log 注入、agent-policy、tool-policy 403、RBAC 403、Agent approve 401、Pending Plan 和 replicas 不变。
- Linux/File Replay 全链通过：strict host key、低权限双 forced-command、任意命令 exit 126、Linux Log 注入、task Workspace、文件正文 Trace/Session 脱敏，原链路不退化。
- Live DeepSeek 未执行：外部请求会发送告警和日志上下文，已有 Key 使用授权不等于项目数据外传授权；安全审查在请求前停止，没有发送数据或暴露 Key。
- 最终残留审计：目标 namespace、managed Pod、四个测试端口、业务进程、SSH Target 和临时目录均为 0；仅保留原项目 kind control-plane，可用内存 6280 MiB、swap 0。
- 详细脱敏记录见 docs/evidence/phase9-full-regression.md。

## Phase 4 本轮记录

- 2026-08-31 用户明确授权 Phase 4 并开启目标模式：实现受限 Linux Host 插件和不带 artifact 前缀的五个原生文件工具。
- 从已完成并同步远端的 `42bb744` 创建 `codex/phase4-linux-files`，创建前工作区干净。
- 固定工具名为 `linux_read`、`list_files`、`read_file`、`search_files`、`write_file`、`edit_file`；不增加任意 Bash/SSH/远端写能力。
- 固定 Linux Host 使用静态 targetId、四个只读 operation、严格 Host Key、低权限 forced-command；SSH 不经过 gVisor，禁止混淆两条信任边界。
- 改造前资源审计：16 CPU、可用内存 7076 MiB、swap 0、Linux 根盘可用 937 GiB、Windows 盘可用 197 GiB、运行容器 0。
- kind 元数据仍指向项目单节点 `sandboxd-control-plane`；只启动了经镜像摘要和 kind 标签核实的该容器，没有创建第二个集群。
- 启动后节点、控制面、Calico、CoreDNS 和 local-path 均恢复 Ready；E2E 前可用内存约 6400 MiB、swap 0、运行容器 1。
- 2026-08-31 改造前真实 Replay E2E 完整通过：Prometheus -> Alertmanager -> agentd、Prometheus/Kubernetes 查询、Pod Log 注入、agent-policy/tool-policy/RBAC 拒绝、Agent approve 401、Pending Plan、replicas 不变和 `Starting gVisor` 均有脚本断言。
- 改造前脱敏证据位于本地 `.cache/agent-demo-evidence/4e111abd02e44029bc50c45d29326351`；该目录不提交 Git，也不冒充 Live LLM 证据。
- E2E 自动清理本轮 `sandboxd-target`、managed Pod、四个服务监听和渲染配置；基线通过后才允许开始 Phase 4 功能代码。
- M2 完成 `LinuxHostClient`、`linux_read` 与静态 Target Registry：固定 `/usr/bin/ssh` argv、strict host key、输出/超时上限，失败不回显凭据路径；Connector 对未知 target/operation 独立返回 `connector-policy`。
- 一次性 Target 基于本地已核实 image ID 的 kicbase v0.0.50，资源限制为 0.25 CPU、192 MiB、64 PID、read-only rootfs，只绑定随机 localhost 端口；低权限 UID 10001 无 sudo，authorized_keys command 与 sshd ForceCommand 双重收口。
- M3 完成 `list_files`、`read_file`、`search_files`、`write_file`、`edit_file`；task 工作区使用 Linux 0700/0600，限制路径/层级/symlink/256 KiB，覆盖使用 SHA256 CAS，写入使用同目录原子替换并返回有界脱敏 diff。
- Trace/Session 对 write content 和 edit old/new text 只保存 bytes + SHA256；read/search/diff 对常见 API Key/Bearer 做模式脱敏。它不是完整 DLP，工作区仍不得存生产秘密。
- 首次 Phase 4 真实 E2E 通过，证据为本地 `.cache/agent-demo-evidence/f2d0ba2e986244f1a55fc9de56c3ca23`：Linux SSH、文件写读、Linux/Pod 两路注入、原 gVisor/Policy/RBAC/审批链均通过，正文未进入 Trace/Session。
- 实测后只剩项目 kind control-plane；`sandboxd-target`、Phase 4 Target、8080/8090/9090/9093 监听和 `/tmp/sandboxd-linux-demo.*` 均无残留。
- Python frozen 单测由 16 增至 22，覆盖 SSH 固定 argv/二次拒绝、路径逃逸/symlink/CAS/权限/任务隔离，以及 Phase 4 Replay 的 8 次 Tool Call 和 Trace 脱敏。
- 实现中记录四个可讲坑：BuildKit 离线 digest 解析、`/run` tmpfs 遮挡、sshd 降权读取 authorized_keys、Docker 随机端口语法；均先做最小实验再修复，没有放宽安全边界。
- 最终静态审计通过：Go `test -p 1`/vet/build、Python compileall/22 tests、全部 Shell 语法、Replay JSON、Markdown 本地链接和 diff check；Go `cmd/internal` 与既有 Kubernetes 安全清单零修改。
- 最终改造后 E2E 再次通过，证据为本地 `.cache/agent-demo-evidence/5b179a176e6a4448b5aa8a7daace3cd0`；新增直接 SSH `cat /etc/passwd` 的远端负向路径返回 126，原 gVisor、Policy、RBAC、审批链继续通过。
- 最终清理审计只剩项目 kind control-plane；目标 namespace、Phase 4 Target、四个服务端口、`/tmp/sandboxd-linux-demo.*`、`/tmp/sandboxd-agent-workspace.*` 和旧测试空目录均无残留。
- 实现代码、测试和脚本已形成提交 `1a4e98f`；三个新增 Shell 脚本通过 Git index 显式保存为 100755，避免 `/mnt/c` 的 `core.filemode=false` 丢失执行位。
- 文档提交 `d71c4e9` 与实现提交 `1a4e98f` 已推送到 GitHub 分支 `codex/phase4-linux-files`；Phase 4 不创建 PR、不合并其他分支，等待用户后续决定。

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
2. 阅读 docs/README.md 和 docs/24-项目全景与心智模型.md。
3. 阅读本文件与 AGENTS.md。
4. 查看 git status、当前分支和最近提交。
5. 只读取本次任务涉及的历史计划、代码和证据。
6. 只继续“下一步”的第一项未完成工作；没有用户授权时不新增功能阶段。

## Phase 2.1 本轮完成

- 新增 ToolResult 双通道：model_content 进入模型，auditDetails 只进入有界本地 Trace。
- 新增 Agent/Turn/Model/Tool/Sandbox 生命周期事件、provider capability、usage、finishReason 和耗时。
- 新增 48 KiB 确定性上下文预算，保留安全 System Prompt 和完整 Tool Call/Result 组。
- 取消后使用独立 10 秒 cleanup Task 释放 sandbox。
- Python 单测由 7 个增至 9 个，覆盖上下文协议、双通道事件和取消只释放一次。
- 明确不实现 Pi Session、steer、follow-up、插件、长期记忆和工具并行。
- 最终验证：Go test/vet/build、Python compileall/9 tests、diff check 全部通过；未启动真实集群或 Live LLM。

## Phase 3 本轮记录

- 2026-08-31 用户明确授权从 Phase 2.1 扩展到 Pi-style 极简 Runtime，并要求进入目标模式持续实现。
- 已创建 `codex/phase3-pi-runtime`，基线为 `787d023`，创建前工作区干净。
- 完成 Phase 3 权威目标、执行规则和实现计划；固定不重写 sandboxd。
- 固定 Pi 只作为源码设计参考，不增加 Node、TypeScript、Pi 或新的 Agent 框架依赖。
- 改造前轻量基线为 9 个 Python 单测通过；显式使用 `uv run --project agentd --frozen`，未启动集群或 Live LLM。
- 用户明确允许后，以 sudo 从 Ubuntu 软件源只安装 `bubblewrap 0.9.0-1ubuntu0.1`，恢复受保护的终端/补丁命令；未修改其他系统配置。
- M1 新增显式受信任 Plugin Registry、Prometheus 插件和 Kubernetes/Plan 插件；没有目录扫描、动态安装或第三方代码加载。
- 模型 Tool Schema 由 Registry 生成，删除 policy.py 中重复 Schema；Python Policy 校验逻辑保持独立。
- Trace 增加插件清单及每步 pluginId/pluginVersion；只读 `GET /api/v1/plugins` 受 API Token 保护，Alert Token 返回 401。
- 现有 Replay 的 Prometheus/Kubernetes/Plan 调用已经真实经过插件分派，危险调用仍在 agent-policy 被拒绝。
- M1 验证为 Python compileall、diff check 和 12 个单测通过；sandboxd 零修改，未启动集群或 Live LLM。
- M2 新增 `runtime/loop.py` 双层循环：内层 Tool Call/steer，外层 follow-up；工具继续顺序执行。
- 新增 `runner.py` 隔离 Sandbox 生命周期；旧 `graph.py` 缩为兼容导入层，Phase 2 调用路径不破坏。
- 专门单测证明 steer 在当前 Turn 后进入下一轮，follow-up 只在自然结束后进入外层循环。
- 删除 LangGraph 直接依赖并离线更新 uv.lock，同时移除四个 LangGraph/ormsgpack 包。
- 原 Replay 注入拦截、Pending Plan、插件 Trace 和取消只释放一次继续通过。
- M2 验证为 frozen 环境 14 个 Python 单测通过；未启动集群或 Live LLM。
- M3 为每次运行生成 taskId、为线性事故上下文生成 sessionId；resume 沿用 sessionId，但创建新 taskId 和新 Sandbox。
- 新增独立 steer/follow-up FIFO、真实 asyncio cancel，以及五个受 API Token 保护的控制/Session API；Alert Token 无权控制任务。
- Session 使用 append-only JSONL 保存脱敏 Header、Command、Transcript 和 Result；不保存 additional_kwargs、隐藏思维或原始凭据。
- Session 目录/文件在 Linux 文件系统上使用 0700/0600；测试发现 WSL 的 Windows TEMP 默认位于 `/mnt/c`，DrvFS 未启用 metadata 时不能验证 POSIX 权限，因此安全状态文件必须放 Linux 文件系统。
- 修复“整段 JSON 先正则脱敏再解析”可能破坏引号或被截断的问题，改为对 Tool 参数叶子递归脱敏并保持合法 JSON。
- M3 最小验证为 frozen 环境 16 个 Python 单测通过；没有启动 kind、Prometheus、Alertmanager 或 Live LLM。
- M4 新增 docs/18 当前 Runtime 学习手册和 docs/19 的 30 个秋招问答；旧 docs/14/15 明确标记为 Phase 2 历史实现与证据。
- README、agentd README、文档索引和项目学习路径已切到双层 Loop、受信任插件与 Session 身份，并保留 LangGraph 重构演进的讲解价值。
- 踩坑新增 40–43：WSL `/mnt/c` 权限语义、结构化 JSON 脱敏、两类 CancelledError、Session/Task/Sandbox 身份分离。
- 文档反查代码时发现 Alert Header 也可能含凭据；已改成结构化逐叶脱敏，并新增 Bearer 告警注解回归断言。
- 最终轻量验证通过：Go test `-p 1`、go vet、go build `-p 1`、Python compileall、16 个 frozen 单测、全部 hack Shell 语法和 diff check。
- 安装 bubblewrap 后，受管理 seccomp 环境会阻塞 FastAPI 线程和 Go 编译子进程；验证改在 WSL 沙箱外串行运行，仍未启动集群或 Live LLM，也没有残留进程。
- Phase 3 四个实现提交 `42c75af..fe0273b` 已推送到 `https://github.com/chx739/sandboxd.git` 的 `codex/phase3-pi-runtime`；未合并或改写其他远端分支。
