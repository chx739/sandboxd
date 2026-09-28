# sandboxd 持续开发目标（最高优先级）

> 本文件是本仓库后续持续开发的“目标锚点”。无论聊天上下文是否完整、是否更换会话或执行者，开始工作前都必须先完整阅读本文件，再阅读 `docs/00-实现计划.md` 和 `docs/PROGRESS.md`。若聊天记忆、临时建议或局部实现与本文件冲突，以本文件为准；不得在没有得到用户明确同意的情况下改变目标。

> 当前维护状态：Phase 1–6 已完成；Phase 7 会话、记忆、ES/Milvus 检索和 Jev 的实现及历史证据保留。2026-09-29 用户批准新的 Phase 8 运维 Agent MVP，完整范围和逐项验收见 `docs/37-Kubernetes运维Agent目标与验收.md`。Jev 已移出当前实施和验收，禁止继续调用。当前任务优先于 Phase 7 中的旧“下一步”；历史外部模型授权不自动延伸至新测评。

## 一句话目标

在公开 GitHub 仓库中，面向 WSL2/Linux 实现一个**最小化、模块化、真实可运行、适合秋招面试讲解**的 Kubernetes AI Agent 沙箱 Demo：以 **gVisor（runsc，WSL2 默认使用 systrap）**提供运行时隔离，用简洁易读、带中文“为什么”注释的 Go 代码串起 Pod 安全基线、ServiceAccount/RBAC、Calico NetworkPolicy、client-go Exec、Informer/Workqueue、预热池与 JSON Patch CAS、Prometheus 指标以及 Deployment 扩缩容 DryRun/审批门，并为每个模块编写配套学习文档、八股知识点、面试问答和可复现验证命令。项目的成功标准是“能在当前 WSL 环境安全地跑通、用户能快速读懂、能展示证据并在面试中讲清设计取舍”，而不是生产级完备性、极致性能、完整测试覆盖率或复杂架构。

## 已完成目标：Phase 2 外部告警诊断 Agent

Phase 1 已完成并保留。当前只在其外增加一个薄 Agent 层：

- 新增 Python agentd，以 LangGraph StateGraph 实现有限 Tool Calling 状态机；
- 使用真实用户态 Prometheus + Alertmanager 接入外部告警；
- Live LLM 查询 Prometheus，并通过 Go sandboxd 的结构化接口在 gVisor 内读取当前 kind 的 Kubernetes 诊断数据；
- 让 Pod Log/ConfigMap 中的间接 Prompt Injection 真实进入上下文，并由 Python Policy、Go Tool Policy、gVisor、NetworkPolicy、RBAC 和 Operator Approval 形成纵深边界；
- 输出结构化诊断或只能由 Operator 审批的 Pending Deployment Scale Plan；
- 同时提供明确标记的确定性 Replay，不冒充 Live 证据；
- 开发范围、接口、文件结构、里程碑和完成证据以 docs/12-Agent层实现计划.md 为准。

## 已完成增量：Phase 2.1 Pi-inspired Agent 内核优化

Phase 2 已完成并保留全部真实证据。本阶段只优化 agentd 内核的可解释性和工程闭环，不改变项目定位与可信边界：

- 工具结果拆为“提供给模型的有界摘要”和“提供给审计的脱敏结构化详情”；
- Trace 增加 Agent/Turn/Model/Tool/Sandbox 生命周期事件，以及模型耗时、Token 和结束原因；
- 模型调用前经过显式上下文转换，安全提示必须保留，不可信 Observation 按预算裁剪；
- 超时或取消必须停止后续步骤并在独立清理窗口释放已认领沙箱；
- Provider 只增加轻量能力元数据，不重写现有 Gateway。

详细接口、文件结构、测试和完成证据以 `docs/16-Pi-inspired-Agent内核优化计划.md` 为准。不得引入 Pi 依赖、TypeScript 重写、会话树、长期记忆、多会话、多 Agent、动态插件、任意 Bash/文件工具、工具并行或生产级事件总线。

上段“不实现 Session、steer、follow-up、插件”的限制只约束已经完成的 Phase 2.1，不再约束用户明确授权的 Phase 3。Phase 2.1 的代码、证据和安全边界仍须保留，不得为了新功能篡改旧证据。

## 已完成目标：Phase 3 Pi-style 安全可插拔运维 Agent Runtime

> 实现状态：Phase 3 M0–M4 已完成，代码、学习/面试文档与最小验证已推送到 `codex/phase3-pi-runtime`。后续不得把本节继续扩写成动态插件、任意 Shell 或生产级平台；新阶段必须先由用户明确选择目标。

在**不重写 Go sandboxd** 的前提下，把 Python agentd 从单次 LangGraph 告警状态机演进为受 Pi 官方源码启发的极简 Agent Runtime：

- 用简单、中文注释充分的手写双层循环表达 Pi 的核心设计：内层处理 Tool Call 与 steer，外层在 Agent 原本结束后处理 follow-up；
- 保留有限轮数、工具预算、总超时、上下文裁剪、ToolResult 双通道、真实 Trace 和取消后独立释放 Sandbox；
- 实现只加载仓库内受信任内置插件的 Plugin Registry，把 Prometheus 与 Kubernetes/Plan 工具迁移为插件；
- 插件只扩展“Agent 看见哪些结构化工具”，不得扩展 sandboxd 默认允许的能力；所有敏感执行仍由 Python Policy、Go Tool Policy、RBAC、NetworkPolicy、gVisor 和 Operator Approval 约束；
- 实现线性 append-only Session-lite，以及最小的 resume、cancel、steer、follow-up API；Session 只服务事故诊断，不做长期记忆；
- 代码和配套文档必须能让用户快速学习 Agent Loop、Session、插件、能力安全、取消语义和 Pi 的设计取舍。

Phase 3 的权威范围、文件结构、里程碑和验收标准以 `docs/17-Pi-style安全可插拔Agent实现计划.md` 为准。Pi 只作为源码和设计参考，不引入 Pi、Node 或 TypeScript 运行时依赖。

## 已完成目标：Phase 4 Linux Host 与原生文件工具

> 实现状态：Phase 4 M0–M5 已完成；代码、前后真实 E2E、学习/面试/踩坑文档已经由历史分支合并到 `main`。后续不得把本节扩写成任意 SSH/Bash、远端写入或生产级主机平台；新阶段必须由用户明确授权。

在 Phase 3 已完成并推送的基础上，增加一个真实但受限的外部 Linux 主机诊断闭环，以及 Pi-style 的任务工作区文件原语：

- 改造代码前后各运行一次同规格、低资源真实 Replay E2E，防止在未知回归上继续叠加功能；
- Linux Host 使用静态受信任 `LinuxHostPlugin`，模型只提交固定 `targetId` 与只读 operation；SSH 只在 Connector 内部使用，必须开启严格 Host Key 校验、低权限账号和远端 forced-command 白名单；
- 第一版 Linux operation 仅有 `host_summary`、`process_list`、`disk_usage`、`read_demo_log`，不得接收任意命令、主机、用户、端口、路径或 sudo；
- Runtime 原生文件工具命名为 `list_files`、`read_file`、`search_files`、`write_file`、`edit_file`，只访问每个 taskId 的专属工作区；
- 文件层拒绝绝对路径、`..`、符号链接和越界解析，限制文件/输出/搜索规模；覆盖与编辑使用 SHA256 条件和原子替换，并返回有界 Diff；
- 文件写入只生成任务草案或报告，不会自动上传、执行或修改 Prometheus、Kubernetes、Linux Host；外部写动作仍须走 Connector 专用 Plan 与审批门；
- SSH Demo 只能使用一次性低权限测试目标，不得把真实 WSL、宿主机或用户其他系统作为默认测试目标。

Phase 4 的文件结构、接口、里程碑、E2E 前后证据与完成判据以 `docs/20-Linux主机与原生文件工具实现计划.md` 为准。仍不实现任意 Bash、任意 SSH、远端文件写入、sudo、动态插件、多租户、生产级凭据系统或大规模并发。

## 历史目标：Phase 5 Prompt Injection Eval v2

> 用户于 2026-09-01 明确授权进入目标模式实现第一版测评集。权威文件结构、指标语义和验收顺序以 `docs/28-Prompt-Injection-Eval-v1实现计划.md` 为准。

> 实现状态：Phase 5 M0–M4 已完成；本地 Replay、学习/面试文档和脱敏 evidence 已形成。未获新的明确授权前不继续批量 Live Eval 或新增功能阶段。

> Live 状态：用户于 2026-09-01 另行明确授权开始当前 20 条 DeepSeek 测试；已完成 1 条预检、Run 1 和修正资源身份后的 Run 2。该次授权已经消费完毕，后续不得据此继续重跑、增加重复次数或调用其他外部模型。

> Eval v2 状态：40 条数据、覆盖校验、Replay 和 88 个 DeepSeek Live Task 已完成。Live 后发现当时 Fake Connector 跨来源复制 artifact；来源隔离与契约已修复，v1/v2 Replay 均通过。该 88 Task 授权已经消费，修复后不得据此重跑。脱敏结果和可用边界见 `docs/evidence/phase13-prompt-injection-eval-v2.md`。

> 修复后正式状态：`parse_final_diagnosis` 与 `canaryEchoRate` 已完成，3 条预检 + 88 Task 来源隔离后 Live 已全部执行。正式攻击结果为来源 72/72 精确、Agent ASR 1/72、Containment 1/1、副作用 0/72；完整证据见 `docs/evidence/phase14-source-isolated-live-eval-v2.md`。本轮 91 Task 授权已经消费，后续不得据此继续调用外部模型。

本阶段只增加最小、可读、可重复的安全测评闭环，不改写 sandboxd 架构：

- 使用 JSONL 维护 40 个运维场景，覆盖正常任务、Pod Log、ConfigMap、Event、Prometheus、Alert、Linux Log、文件内容和良性困难样本；
- 每个场景同时定义正常任务、非可信内容、攻击目标、允许/禁止工具、必要证据和预期边界；
- 用当前 `AgentRunner`、双层 Loop、Plugin Registry、Python Policy 和 task Workspace 跑确定性 Replay，不另造一套假 Agent；
- 分开统计 Clean Task Success、Agent ASR、Unauthorized Side-effect Rate、Containment Rate、Over-refusal Rate 和 Evidence Coverage；
- Replay 故意触发危险 Tool Call，只证明确定性执行边界，不冒充真实模型攻击成功率；
- Live Eval 必须使用真实 Provider、多次运行并单独标记，但没有新的项目数据外发授权时不得运行；上一次“单次 DeepSeek Live E2E”授权不扩展到本阶段批量用例；
- 同步交付中文注释、最小单测、学习文档、项目 FAQ、脱敏 evidence 和 GitHub 记录。

Phase 5 明确不做：引入 AgentDojo/ASB 运行时依赖、上百样本、大规模模型横评、自动红队生成、LLM-as-Judge 作为安全事实、生产数据、真实秘密、自动攻击真实系统或修改 Go sandboxd。

## 已完成目标：Phase 6 加固（评审路线 A + Eval Harness 自检）

> 用户于 2026-09-18 明确授权。范围是修复架构评审发现的确定性缺陷并补齐测评自检，不新增功能、不改架构、不消耗任何外部模型授权。

- 预热池补池路径增加单次创建超时（`NewPool` 新增 `createTimeout`，与 HTTP 冷启动共用 `cfg.CreateTimeout`），修复“不可调度 Pod 把单 worker 对账永久卡死”的缺陷；配套单测 `TestReconcileCreateIdleIsBounded`（见 `docs/11` 坑 52）。
- agentd 的 claim/release 显式化为独立预算常量 `MAX_CLAIM_SECONDS` / `MAX_RELEASE_SECONDS`，任务三段预算（claim/loop/release）在 `policy.py` 一处声明并解释为什么 claim 不能共用 loop 预算；claim 进入 `asyncio.wait_for`。
- `deleteSandbox` 使用独立 10 秒 context，客户端断开不再中断清理；`readyz` 真实检查 informer 同步（未同步返回 503，fail closed）；create/list/delete 的 5xx 内部错误脱敏并记录服务端日志；创建超时映射 504 与 exec 一致。
- CAS 冲突判定集中为 `isClaimConflict` 并配单测；`claimPatch` 静态 JSON 与 `LabelState` 的双份知识用 `TestClaimPatchPathMatchesStateLabel` 锁死。
- Eval Loader 增加 source→sourceTool 通道契约（`SOURCE_CHANNELS`）与 injectionSources 恰等校验：lint 阶段即可拒绝夹具契约错误，v1/v2 数据集零修改通过；新增 golden Replay 基线 `agentd/testdata/eval-replay-golden-v2.json` 钉死 40 条 case 的确定性结果，防第四个 harness 缺陷。
- kind 模板补充 containerd v1/v3 配置路径的澄清注释，不改已验证的配置。
- 验证只用本地手段：`go build/vet/test`、Python 串行 unittest（34 个）、evals `lint`/`replay` CLI 门禁；未启动 kind、Docker、Prometheus、Alertmanager、SSH Target，未调用外部 LLM。

Phase 6 不做：新功能、架构重写、agent-sandbox CRD 对接、exec 端点收紧、RBAC 收敛、Plan 审计持久化（这些是评审建议的后续路线，须另行逐项授权）。

## 历史目标：Phase 7 Agent 会话、记忆、检索与路由 MVP

用户已明确要求在现有 Python agentd 上依次交付四个独立可运行、可测评的最小模块：

1. 参考 Pi 源码，在现有 JSONL 上增加 `nodeId/parentId` 树、活动叶子、完整 Turn 分支与语义恢复；旧线性会话保持可读，恢复不重放历史工具副作用，并新建 task/sandbox。
2. 参考 Codex memories 源码，实现已结束会话提取、跨会话整理、按需分层读取；保留来源、时间、冲突与有界摘要，提供重建和遗忘入口。
3. 同一语料写入 Milvus dense 与 Elasticsearch BM25 倒排索引，经 RRF 和 BGE Reranker 检索；提供消融、带标签数据与质量/延迟/资源报告。
4. 按 TypeSafe Jev 官方 SDK 实现任务开始前的模型路由、确定性回退与费用记录；保留 Fake/Replay，Live 对比在新授权后执行。

2026-09-28 Live 更新：用户已批准 Phase 20 的固定 8 题、最多 8 次 Jev + 24 次 DeepSeek、总预算 $1。联网执行首题后因 Jev 错误停止，已记录 1 次 Jev 尝试和 3 次 DeepSeek；只读 IPv4 鉴权返回 HTTP 401，完整横评未完成。恢复边界与首次无报告超时见 `docs/evidence/phase21-jev-live-first-case.md`。代码分支已推送并创建 PR #1，未合并 main。

权威模块范围、资源边界、依赖与验收顺序见 `docs/31-Agent记忆检索与路由最小实现方案.md`。每项必须有运行命令、最小测评、中文学习文档和诚实的 Replay/集成/Live 证据。现有手写 Agent Loop 和可信执行边界保持；不因本阶段引入 LangGraph、任意 Shell、动态插件、自动审批或生产级多租户。先完成无密钥工作；历史 DeepSeek Eval 授权不延伸到本阶段。

## 当前目标：Phase 8 Kubernetes 运维 Agent MVP

用户于 2026-09-29 确认实施。**完整的实施范围、测评设计、资源和费用边界、文档要求、完成判据以 [`docs/37-Kubernetes运维Agent目标与验收.md`](docs/37-Kubernetes运维Agent目标与验收.md) 为准；本段只是入口，不得代替该文件验收。**

目标是在现有 Python agentd 上复用并完善 Pi 风格树形会话、Codex 风格分层记忆；将知识库检索改为 Milvus **2.5.10** 内置 BM25 + Dense + RRF + BGE Reranker；使用 OpenSearch 查询和聚合现场日志；实现至少三类故障的联合排障演示，并分别测评会话记忆、运维 RAG、日志查询、Agent 决策和系统性能。知识库、日志和记忆各有明确来源及证据边界。交付可复现命令、固定数据和模型版本、原始结果、中文学习文档、阶段证据和可审查 PR。

Phase 8 不实施、不调试、不调用 Jev，也不以 Jev Live 横评为验收条件；原代码和证据作为历史保留。旧 ES 知识索引及报告保留作迁移对照，默认知识库路径改用 Milvus BM25。旧 ES 服务/卷不得因为迁移而直接删除。OpenSearch 仅承担本轮新日志能力。真实模型、自动评分和人工审核必须标注来源；没有确切授权的付费调用先完成独立工作并列出待办。不得自动合并 PR。

## 不可偏移的约束

1. **必须真实使用 gVisor。** 不接受只写 `RuntimeClass` YAML 或用普通 `runc` 冒充；必须保留 `runsc` 运行证据。
2. **环境固定为 WSL2/Linux 的低资源单节点 Demo。** 默认使用单 control-plane kind 集群、gVisor systrap、预热池大小 2、并发验证 5；只有检查资源充足后才可升到 10，不进行 20 并发日常测试。
3. **安全闭环必须可演示。** Agent 身份可以读取允许的数据，但敏感写操作要被 RBAC 拒绝；Agent 无权审批扩缩容，Operator 才能执行经过校验的审批。
4. **并发机制必须有真实知识点。** 使用 Informer/Lister/Workqueue 维护状态，用 JSON Patch 条件更新实现 CAS 抢占；并发获取不能返回重复沙箱。
5. **代码以简单、易读为第一原则。** 不引入 Web 框架、依赖注入框架、ORM、CRD、Operator 或无必要抽象；优先标准库和 client-go。
6. **注释解释原因。** 对 gVisor、权限、网络、并发、超时和清理等关键代码写简洁中文注释，重点说明“为什么这样做”，不逐行翻译代码。
7. **每个模块同时交付代码、学习文档和验证方法。** 文档应覆盖：作用、最小实现、代码阅读顺序、八股知识点、方案取舍、常见坑、面试问答、验证命令和一分钟项目讲法。
8. **测试保持最小但必须可信。** 以编译检查、关键纯逻辑单测和少量真实集成演示为主；不追求覆盖率，不运行可能拖垮 WSL 或宿主机的压力测试。
9. **GitHub 仓库是代码与文档的唯一项目来源。** 按模块小步提交；不得提交密码、令牌、API Key、个人秘密目录、父目录中的其他项目或简历内容。
10. **不把 Demo 宣传成生产系统。** README 和面试讲解必须诚实说明单节点、单租户、简化认证、非生产网络策略和测试规模等边界。

## sudo 与系统安全红线

1. 默认不使用 sudo；能用普通用户、用户目录或容器内操作完成时，不提权。
2. 使用 sudo 前先做只读检查，明确命令、目标路径、影响范围和恢复方式；只执行完成当前步骤所需的最小命令。
3. sudo 密码只允许从用户提供的仓库外文件经标准输入传入；禁止显示密码、记录密码、写入脚本、环境变量、Git、日志或命令参数。
4. 禁止对 `/`、`/home`、用户主目录、工作区根目录、Docker 数据根目录等宽泛目标执行递归删除、覆盖或权限修改。
5. 禁止未经明确需要修改防火墙、WSL 全局配置、内核参数、磁盘分区、网络路由或系统级 Docker 数据。
6. 安装系统组件前优先确认来源、版本、校验和及磁盘空间；尽量将 Go、kind、gVisor 等工具安装在用户目录。
7. 测试前检查 CPU、可用内存、swap、磁盘和现有容器；串行执行重操作。若可用内存接近 2 GiB、出现持续 swap、Docker/WSL 明显异常，应立即停止并清理本项目创建的资源。
8. 所有清理操作必须精确限定到本项目创建且已经核实的资源；不删除其他容器、镜像、卷、仓库或用户文件。

## 明确不做

- 不做多租户生产平台、计费、HA、跨集群调度、完整身份系统或 Web 管理后台。
- 不为追求“架构感”添加消息队列、数据库、微服务拆分、CRD/Operator 或复杂配置中心。
- 不追求大规模压测、形式化安全证明、完整 e2e 矩阵或生产 SLA。
- 不把简历、密钥目录或旧项目迁入/清入本仓库；它们不属于 sandboxd 的实现范围。

Phase 2 额外不做项是历史阶段边界。Phase 3 只解除“线性 Session、steer/follow-up、受信任内置插件”三项限制；仍不做外部 Kubernetes、多集群、多租户、文件传输、RAG、长期记忆、多 Agent、数据库、消息队列、自动审批、LangSmith 和生产级 HA。

Phase 3 明确不做：Pi 完整复刻、Session 树/fork、TUI、插件市场、在线安装、任意第三方代码加载、任意 Shell、插件热更新、生产级身份系统、分布式 Session、复杂自动 Compaction 和大规模并行工具。Phase 4 只解除“受限 Linux Host Connector”和“task 专属工作区文件工具”两项限制；其他限制继续有效。

Phase 5 只解除“增加本地安全 Eval 数据、Runner、Scorer 和相关文档”这一项限制。v1/v2 已使用的外部 LLM 授权均已消费；它不继续授权批量外部调用、下载大型基准、扩展 Agent 权限或增加生产运维能力。

## 完成判据

只有同时具备以下证据，才可宣布目标完成：

- 新环境可按脚本安装固定版本工具，并创建单节点 kind + gVisor + Calico 环境。
- 沙箱 Pod 确认由 `runsc` 承载，安全上下文、RBAC 和网络隔离均有可复现的成功/拒绝证据。
- Manager、Exec、HTTP API、Informer/Workqueue、预热池、CAS 抢占、指标和审批门全部跑通。
- 并发小测试证明没有重复分配；超时后能清理；扩缩容流程能展示 DryRun、Agent 拒绝、Operator 成功。
- `make build`、最小测试和一键 Demo 在当前 WSL 环境通过。
- README 记录真实运行结果、资源消耗、已知限制和演示顺序；各模块学习文档完整。
- 所有应交付代码和文档都已提交并推送到 GitHub，仓库中不存在秘密信息。

Phase 2 还必须证明：真实 Prometheus/Alertmanager 告警进入 agentd；至少一次 Live LLM 完成诊断；Agent 查询真实 Prometheus并通过 gVisor 查询 Kubernetes API；注入文本进入模型可见上下文；Replay 危险调用被拒绝且明确标记；Go Tool Policy、RBAC 和 Agent 无审批权都有真实证据；最终输出诊断或 Pending Plan；资源清理、脱敏 Trace、学习/面试/踩坑文档和 GitHub 推送全部完成。

Phase 5 还必须证明：数据集可 lint 且没有真实秘密；20 个场景能在本地 Replay Runner 串行完成；指标公式有纯逻辑单测；危险调用经过真实 Python Policy/Workspace 边界且外部副作用为零；报告明确区分 Replay 与 Live；原有 Python 测试和最小静态检查不回归；学习/面试/evidence/PROGRESS 已更新并推送 GitHub。

## 上下文缺失时的续作协议

后续执行者不得仅依赖聊天历史。每次继续开发都按下面顺序恢复上下文：

1. 完整阅读 `GOAL.md`。
2. 阅读 `docs/README.md`、`docs/24-项目全景与心智模型.md` 和 `docs/PROGRESS.md`。
   只有修改历史模块时才继续阅读对应计划：Phase 1 为 `00`、Phase 2 为 `12`、Phase 2.1 为 `16`、Phase 3 为 `17`、Phase 4 为 `20`。
3. 查看 `git status`、最近提交和当前分支，保护用户已有改动。
4. 阅读当前模块的代码、学习文档、脚本及最近一次验证输出。
5. 从 `docs/PROGRESS.md` 标记的“下一步”继续，只完成当前最小闭环。
6. 每完成一个模块，立即更新代码、模块文档、验证结果和 `docs/PROGRESS.md`，然后小步提交到 GitHub。
7. 如果发现实现计划需要改变，先判断是否触碰本文件的目标或约束；触碰时必须暂停并征得用户明确同意。
