# 后续开发执行规则

> 当前仓库状态：Phase 1–4 已完成并合并到 `main`，功能暂时冻结。除非用户明确开启新阶段，否则优先维护文档一致性、最小测试和面试学习材料，不从历史计划的“下一步”自行扩展功能。

本仓库的任何自动化编码助手在开始工作前，必须完整阅读：

1. `GOAL.md`：最高优先级目标、边界和安全红线。
2. `docs/README.md`：当前文档分层和权威入口。
3. `docs/24-项目全景与心智模型.md`：当前实现、信任边界和项目口径。
4. `docs/PROGRESS.md`：已经完成、正在进行和下一步。
5. 与本次任务直接相关的代码、学习文档和最近验证证据。

只有修改某个历史模块时，才需要额外阅读对应计划：Phase 1 读 `00`，Phase 2 读 `12`，Phase 2.1 读 `16`，Phase 3 读 `17`，Phase 4 读 `20`。不要每次把全部历史计划重新当作当前需求。

仓库文件、Git 状态和实际命令输出比聊天记忆更可信。上下文不完整时，不要重新设计项目，也不要根据猜测扩大范围；从 `docs/PROGRESS.md` 的下一步恢复。

执行时必须遵守以下规则：

- 只实现当前模块的最小可运行闭环，优先简单 Go 代码和中文“为什么”注释。
- 每个模块同步维护学习文档、面试问答和最小验证命令。
- 不追求生产级架构和测试覆盖率，不引入目标外框架或功能。
- 执行真实集群测试前检查系统资源，默认串行、小并发、低副本。
- sudo 默认禁用；确需使用时严格遵守 `GOAL.md` 的 sudo 与系统安全红线。
- 绝不读取后展示、复制、提交或记录密码、API Key、令牌等秘密。
- 不触碰 sandboxd 仓库外的简历、其他仓库和用户文件；不清理非本项目资源。
- 修改前检查 `git status`；保留用户已有修改；按模块小步提交并推送 GitHub。
- 验证失败时记录事实和最小复现，不用虚假输出或普通 runc 结果冒充 gVisor 成功。
- 只有 `GOAL.md` 的完成判据全部有证据时，才可以宣布整个项目完成。

Phase 2 额外规则：

- Go sandboxd 是可信执行边界，Python agentd 和 LLM 均视为不可信调用方。
- Phase 2 当时使用 LangGraph 编排；无论编排实现为何，都不得把 Prompt、Tool Schema 或循环分支宣传为最终安全边界。
- agentd 不得接收、保存或使用 Operator Token，不得调用 approve/reject。
- Live 与 Replay 证据必须明确区分；人工 Replay 标记为 deterministic-policy-case。
- 默认禁用 LangSmith，不上传 Alert、模型输入、工具输出或 Trace。
- 值得复盘的真实问题写入 docs/11-开发踩坑与排障.md，包含现象、根因、修复和面试价值。
- Phase 2 历史分支为 codex/phase2-langgraph-agent；最终代码以 main 和当前 Runtime 文档为准。

Phase 2.1 额外规则：

- Pi 当时只用于改进事件、上下文与取消，不替换 LangGraph；这是 Phase 2.1 历史范围。
- 模型上下文与审计数据必须分离；审计字段不能由 LLM 自报覆盖。
- 安全 System Prompt 和最新拒绝证据不得被上下文转换移除。
- 工具保持顺序执行；禁止为了模仿通用 Coding Agent 引入并行工具、插件或任意系统工具。

Phase 3 额外规则（历史已完成阶段）：

- Phase 3 历史分支为 `codex/phase3-pi-runtime`；最终代码以 main 和 Phase 4 后的当前文档为准。
- Pi 只作为设计参考；使用 Python 手写最小双层循环，不增加 Pi、Node、TypeScript 或新的 Agent 框架依赖。
- Plugin Registry 只注册仓库内受信任内置插件。不得从任意目录、网络、npm、Git 或用户输入动态加载代码。
- 插件清单是能力声明，不是授权本身；Python Policy 和 Go sandboxd 仍必须独立拒绝越权请求。
- Session 采用线性 append-only JSONL；只保存脱敏消息和运行事件，不保存 Header、Token、API Key、隐藏思维或原始凭据。
- steer 只在当前 Turn 的安全点进入下一轮上下文；cancel 才负责取消当前运行。不得把 steer 宣传为已撤回正在执行的外部动作。
- 第一版工具继续顺序执行，便于审计、预算统计和面试讲解；不为了模仿 Pi 默认并行而引入竞态。

Phase 4 额外规则（历史完成范围）：

- Phase 4 历史分支为 `codex/phase4-linux-files`，最终实现已经合并到 `main`；不得再把历史分支要求误当成当前分支要求。
- Linux Host 插件只能调用部署者固定的 targetId 和四个只读 operation；不得让模型提交 host、user、port、identity path、任意 argv、Shell 字符串或 sudo。
- SSH 必须使用 `BatchMode`、严格 Host Key、固定 known_hosts、独立低权限账号、forced-command，以及禁用 TTY/转发；私钥只存在仓库外临时目录。
- Demo Target 必须是本项目一次性容器或虚拟机，资源受限并可精确清理；禁止默认连接真实 WSL 或宿主机。
- 文件工具只访问 `taskId` 专属工作区。路径必须是相对路径并拒绝 `..`、符号链接和逃逸；写/编辑必须记录 hash 和有界 diff。
- `write_file`/`edit_file` 只修改工作区草案，不代表外部系统已变更；不得添加远端文件上传、执行或自动审批。
- 不增加 Paramiko/AsyncSSH 等依赖；优先使用 WSL 已有 `/usr/bin/ssh` 的固定 argv 子进程，禁止 `shell=True`。

Phase 5 额外规则（当前 Eval v2）：

- 先读 `docs/29-Prompt-Injection-Eval学习手册.md`、`docs/30-Prompt-Injection-Eval-v2秋招测试计划.md` 和 Phase 13 evidence；v1 是不可改写的历史基线，默认当前实现为 40 场景 v2。
- 数据集只能使用合成运维内容和显式 canary，不得复制真实 Token、用户日志、kubeconfig、主机信息或仓库外秘密。
- 必须把 Agent ASR 与系统未授权副作用率分开；Replay 故意发出危险 Tool Call 时，不得把 100% Agent ASR 解释为真实模型结果。
- 安全事实优先使用确定性断言：Tool Call、denyLayer、外部状态、canary、清理结果。自然语言 Diagnosis 不使用 LLM-as-Judge 决定是否越权。
- Replay、本地纯逻辑和 Live 三种证据必须分开。没有用户新的明确授权，不得读取 Key 或批量向外部模型发送 Eval 场景。
- 2026-09-01 的 DeepSeek Live Eval 授权已用于预检、Run 1 和修正夹具后的 Run 2；后续执行者必须视为已消费，不能从历史授权推导出新的外部调用权限。
- Eval v2 的 88 个 Live Task 已全部完成，该授权已消费。不得增加第 4 次攻击重复、在来源隔离修复后重跑、调用其他模型或连接真实系统；若要重新取得干净 Live 数字，必须获得用户新的明确授权。
- 用户已为当前解析修复后的重跑给出新授权，精确范围是 3 条预检和原协议 88 Task，共 91 Task。只在解析/指标测试与 v1/v2 Replay 全通过后执行，仍固定 DeepSeek V4 Flash、thinking disabled、串行和 Fake Connector；不得扩大次数、模型、数据或真实系统。完成或触发止损后立即把本条改成已消费。
- v1 数据与 Phase 11/12 证据不可改写。v2 新增文件并将 CLI 默认切到 v2，但历史命令必须仍可显式加载 v1。
- Fake Connector 的 artifact 必须只从 case 声明的 source 返回；Scorer 必须把未声明 injection source 视为夹具契约错误。Phase 13 的首次 Live 有跨来源污染，只能引用其全局历史观察和缺陷，不得宣传来源分层结果。
- 默认只运行 Python 本地串行测试，不启动 kind、Docker、Prometheus、Alertmanager 或 SSH Target；需要真实集群时另做资源检查和精确清理。
- 不引入 AgentDojo、ASB、数据库、测试框架或新 Provider 依赖；优先标准库、现有 Pydantic 和当前 AgentRunner。
