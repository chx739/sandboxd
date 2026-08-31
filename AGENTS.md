# 后续开发执行规则

本仓库的任何自动化编码助手在开始工作前，必须完整阅读：

1. `GOAL.md`：最高优先级目标、边界和安全红线。
2. `docs/00-实现计划.md`：模块划分与实现顺序。
3. `docs/12-Agent层实现计划.md`：Phase 2 架构、接口、安全边界和完成证据。
4. `docs/16-Pi-inspired-Agent内核优化计划.md`：Phase 2.1 的固定范围、接口和验证顺序。
5. `docs/17-Pi-style安全可插拔Agent实现计划.md`：Phase 3 的固定范围、文件结构和验收顺序。
6. `docs/PROGRESS.md`：已经完成、正在进行和下一步。

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
- Phase 2 历史分支为 codex/phase2-langgraph-agent；当前分支以 Phase 3 规则为准。

Phase 2.1 额外规则：

- Pi 当时只用于改进事件、上下文与取消，不替换 LangGraph；这是 Phase 2.1 历史范围。
- 模型上下文与审计数据必须分离；审计字段不能由 LLM 自报覆盖。
- 安全 System Prompt 和最新拒绝证据不得被上下文转换移除。
- 工具保持顺序执行；禁止为了模仿通用 Coding Agent 引入并行工具、插件或任意系统工具。

Phase 3 额外规则：

- 当前分支固定为 `codex/phase3-pi-runtime`；Phase 2.1 的禁止插件/Session 条款是历史阶段限制，以 GOAL.md 的 Phase 3 明确授权为准。
- Pi 只作为设计参考；使用 Python 手写最小双层循环，不增加 Pi、Node、TypeScript 或新的 Agent 框架依赖。
- Plugin Registry 只注册仓库内受信任内置插件。不得从任意目录、网络、npm、Git 或用户输入动态加载代码。
- 插件清单是能力声明，不是授权本身；Python Policy 和 Go sandboxd 仍必须独立拒绝越权请求。
- Session 采用线性 append-only JSONL；只保存脱敏消息和运行事件，不保存 Header、Token、API Key、隐藏思维或原始凭据。
- steer 只在当前 Turn 的安全点进入下一轮上下文；cancel 才负责取消当前运行。不得把 steer 宣传为已撤回正在执行的外部动作。
- 第一版工具继续顺序执行，便于审计、预算统计和面试讲解；不为了模仿 Pi 默认并行而引入竞态。
