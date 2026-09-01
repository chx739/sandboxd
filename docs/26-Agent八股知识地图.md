# Agent 八股知识地图

> 面向秋招：先回答“概念是什么”，再回答“本项目为什么这样选”，最后主动说边界。示例均对应当前手写 Pi-style Runtime。

## 1. LLM、Workflow 和 Agent 的区别

- LLM：根据输入生成输出，本身不拥有外部世界状态。
- Workflow：开发者预先固定节点和分支，执行路径主要由代码决定。
- Agent：模型能根据上下文选择工具和下一步动作，路径具有运行时动态性。

本项目介于 Workflow 和通用 Agent 之间：模型动态选择九个工具，但轮数、预算、权限、目标系统和写动作都被程序固定。

面试回答：

“我保留了 Agent 的动态 Tool Calling，但把能力空间做窄。这样既能展示 ReAct，又不会让模型获得任意 Shell 或通用 Kubernetes Client。”

## 2. ReAct 和 Tool Calling

ReAct 的抽象是：

~~~text
Observation -> Reason/Decision -> Action -> Observation -> Final
~~~

Tool Calling 是模型输出结构化函数名和参数的协议。本项目不保存或要求隐藏 Chain of Thought，只保留：

- 模型可见消息；
- Tool Call；
- Tool Result；
- Denial；
- 生命周期事件；
- 最终结构化 Diagnosis。

因此可以说是“ReAct 风格 Tool Calling 循环”，不能说保存了完整思维链。

## 3. 一次 Turn 是什么

当前定义：

~~~text
一次模型调用 + 该响应产生的顺序 Tool 执行
~~~

模型没有 Tool Call 时，当前内层循环准备结束。Turn 边界是 steer 能安全进入的位置，也方便统计 iteration、模型 usage 和工具预算。

## 4. 为什么手写循环，而不是 LangChain/LangGraph

### LangChain 高层 Agent

优点：开发快、Provider 和 Tool 生态丰富。

缺点：默认循环和状态可能较隐式，面试时容易只会调用框架 API。

### LangGraph

优点：显式 State、节点和条件边，适合复杂状态机、持久化和人工中断。

缺点：当前循环很小，图和 checkpoint 会增加抽象；项目还会与框架版本耦合。

### 手写循环

优点：约 1 个核心文件就能看到循环、预算、拒绝回灌、steer/follow-up 和 finalize；安全边界不依赖框架。

缺点：需要自己处理协议闭合、上下文、取消、事件、恢复和测试。

本项目先用 LangGraph 跑通 Phase 2，再抽象 ToolResult/Event/Context/Cleanup，最后手写循环并删除 LangGraph 依赖。这是“先借框架验证，再在范围稳定后收回控制流”。

## 5. Pi-style 双层循环

内层处理正常 Agent 运行和 steer，外层处理任务自然结束后的 follow-up：

~~~text
outer:
  inner:
    model -> tool calls -> sequential results -> steer
  if follow-up:
    continue outer
  else:
    finish
~~~

这不是完整复刻 Pi。项目只借鉴 Transcript、Tool、事件、steer/follow-up 和 Session 分层思想。

## 6. steer、follow-up 和 cancel

| 操作 | 何时进入 | 是否撤销已执行工具 | 语义 |
|---|---|---|---|
| steer | 当前 Turn 完成后的安全点 | 否 | 调整正在运行任务的后续方向 |
| follow-up | Agent 原本自然结束时 | 否 | 在同一 Session 追加新问题 |
| cancel | 立即取消运行 Task | 不能回滚外部副作用 | 终止控制流并触发清理 |

最容易答错的是把 steer 说成实时中断。它只能影响下一轮模型上下文。

## 7. Tool Schema、校验和授权

三者不同：

- Tool Schema：告诉模型字段和类型，改善结构化输出；
- 参数校验：检查枚举、长度、格式和预算；
- 授权：判断这个身份是否允许对这个目标执行动作。

Schema 不是安全边界，因为攻击者可以跳过模型直接调用 HTTP API。项目因此保留 Python Policy、Go Tool Policy、Connector 约束和 Kubernetes RBAC。

## 8. Plugin、Tool 和 Connector

- Tool：模型可调用的最小能力，例如 kubernetes_read。
- Plugin：把 Manifest、Tool Schema 和执行函数组织在一起。
- Connector：连接具体外部系统的可信客户端，例如固定 Prometheus URL 或 SSH Target Registry。

Plugin Manifest 是能力说明，不是权限票据。Plugin 也不应直接得到 Operator Token、任意 kubeconfig 或任意文件系统。

当前 Registry 只显式注册仓库内内置插件，不扫描目录、不在线安装、不动态执行第三方代码。

## 9. MCP 和本项目 Plugin 的关系

MCP 主要标准化 Agent 与工具服务的发现和调用；本项目 Plugin 主要解决进程内模块化和面试可读性。

如果未来接 MCP：

~~~text
LLM/Runtime -> MCP Client -> MCP Server -> 本项目 Policy/Connector -> 目标系统
~~~

MCP 负责协议互通，不应替代 RBAC、审批和目标级授权。当前工具数量少，直接引入 MCP 的收益小于复杂度。

## 10. ToolResult 为什么分双通道

同一次工具执行有两个消费者：

- modelContent：给模型的短、稳定、有界摘要；
- auditDetails：给 Trace 的脱敏结构化证据。

如果把完整原始响应都塞给模型，会增加 Token、注入面和上下文截断；如果 Trace 只存模型摘要，又缺少排障证据。

双通道原则：

~~~text
模型需要“足够决策”，审计需要“足够复盘”，两者不等价。
~~~

## 11. Context Engineering

Context 不等于把所有历史消息拼起来。需要处理：

- System Prompt 必须保留；
- 初始 Alert 要保留；
- Tool Call 与对应 Tool Result 不能拆散；
- 单条 Observation 和总字符数有预算；
- 较旧的完整协议组可裁剪；
- 不用另一个 LLM 自动摘要，避免成本和摘要注入。

本项目采用确定性字符预算，因为任务最多有限轮数，不需要复杂 Compaction。

## 12. Memory 和 Session

常见层次：

| 层次 | 含义 | 本项目 |
|---|---|---|
| Working Memory | 当前上下文窗口 | messages + transform |
| Session Memory | 一段会话可恢复记录 | append-only JSONL |
| Long-term Memory | 跨会话知识/偏好 | 未实现 |
| Semantic Memory/RAG | 向量检索历史资料 | 未实现 |

Session-lite 保存脱敏 Transcript 和 Command，resume 创建新的 Task/Sandbox。它不是进程快照，也不是长期记忆。

JSONL 的好处是追加简单、局部损坏容易定位、教学可读；缺点是查询、并发写、索引和事务能力弱。

## 13. Prompt Injection

### 直接注入

用户直接要求模型忽略规则。

### 间接注入

模型读取网页、日志、ConfigMap、Issue 或 Tool Result 时，数据中混入了伪装成指令的文本。

运维 Agent 风险更高，因为日志和资源描述天然来自不可信工作负载。

System Prompt 只能降低模型服从概率。确定性防护应包括：

- 数据来源标记；
- 最小 Tool 集；
- 参数白名单；
- 服务端重复校验；
- 最小凭据和网络；
- 高风险动作审批；
- 有界输出和脱敏审计。

本项目的结论是 containment，不是 prevention：注入可能影响模型，但不能越过最终执行边界。

## 14. 为什么 Agent 不能拿任意 Bash

任意 Bash 把“参数空间”扩大为整个操作系统：

- Shell 注入和转义复杂；
- 可扫描凭据、网络和进程；
- 工具 Schema 无法表达真实权限；
- 审计难判断命令副作用；
- Prompt Injection 的后果急剧放大。

当前 Linux Tool 只接受 targetId 和 operation；文件 Tool 只写 task Workspace；Kubernetes Tool 只接受固定 operation。这是 capability-based 的窄接口。

## 15. 身份和能力安全

Agent 安全不只是“谁登录了”，还包括“这个身份拿到什么能力”：

- Agent Token 能提交 Plan，不能批准；
- Operator Token 不进入 agentd；
- Sandbox SA 只读且不含 Secret；
- SSH 远端用户无 sudo；
- PluginContext 只注入需要的窄客户端；
- 每个 task 有独立 Workspace。

生产化还需要 user/tenant、资源归属、目标级身份、凭据轮转和不可抵赖审计。

## 16. 取消、超时和补偿动作

取消控制流不等于撤销副作用。Tool 已经完成的 HTTP/SSH 操作不会自动回滚。

本项目的保证：

- 停止后续模型和工具步骤；
- Runner finally 释放 Sandbox；
- 清理使用独立 Task + shield + 10 秒上限；
- Session 尽力保存最后一个完整 Transcript；
- 写操作本来就只形成 Pending Plan。

需要幂等、事务或补偿的生产写操作，应由专门 Workflow 设计，而不是依赖 asyncio cancel。

## 17. 顺序工具和并行工具

顺序执行的收益：

- 工具结果顺序确定；
- Tool Call/Result 协议容易闭合；
- 预算和审计简单；
- 一个工具的输出可以影响后一个工具；
- 降低目标系统瞬时压力。

并行适合相互独立、只读、延迟占主导的调用，但需要处理竞态、取消、部分失败和结果合并。当前低资源 Demo 刻意顺序执行。

## 18. Live、Replay、Mock 和 Eval

- Live：真实模型调用，能观察模型行为，但非确定且依赖网络/Key。
- Replay：固定模型响应，仍走真实 Runtime/Policy/Tool 链，适合确定性回归。
- Mock：通常只替换局部依赖，适合单元测试。
- Eval：用数据集和评分标准系统衡量能力、安全、成本和延迟。

Replay 不能证明模型会正确选工具，只能证明给定 Tool Call 时系统如何处理。Live 三次实验中工具选择不一致，正说明模型能力不能作为权限边界。

如果继续做 Eval，可测：

- 诊断正确率；
- 必要工具覆盖；
- 危险调用率；
- 被拒绝后的恢复率；
- Token/延迟；
- 清理成功率；
- Trace 完整率。

## 19. Agent 可观测性

至少需要三类数据：

- Transcript：模型可见消息；
- Trace/Event：Agent、Turn、Model、Tool、Sandbox 生命周期；
- Metrics：次数、耗时、拒绝、资源池状态。

不要保存隐藏思维；不要相信模型自报 usage、evidence 或 planId；不要把 Token/Header 原文写入 Trace。

## 20. Agent 最终输出为什么也不可信

模型可能声称“已缩容”“已审批”或伪造 evidence。当前 finalize 会把模型自报的 evidence、deniedActions、planId 清空，再从真实执行 State 回填。

一般原则：

~~~text
模型负责自然语言解释，程序负责事实字段。
~~~

## 21. 常见 Agent 架构取舍

### Plan-and-Execute

适合长任务、步骤多、需要提前审查计划。当前短诊断链不需要额外模型 Plan；项目已有安全治理意义上的 DryRun Plan，避免概念冲突。

### RAG

适合大量静态文档和历史 runbook。当前权威数据来自实时 API，Tool Calling 是精确检索层。RAG 还会新增间接注入面。

### 多 Agent

适合可并行的角色分工，但会增加身份、上下文传播、成本和审计复杂度。当前单事故诊断没有足够收益。

### 动态插件

适合平台生态，但等价于引入代码供应链和宿主权限问题。当前只做静态内置插件。

## 22. 高频十问速答

### 1. 这是不是 ReAct？

是 ReAct 风格 Tool Calling，但不保存隐藏思维，只记录可见 Tool Call/Result 和事件。

### 2. 为什么不用 LangGraph？

Phase 2 用过；范围稳定后手写双层循环更易读，并减少框架依赖。复杂持久化图时 LangGraph仍有价值。

### 3. Tool Schema 能防越权吗？

不能，它只是模型协议。服务端 Policy、Connector 和 RBAC 才是确定性边界。

### 4. steer 能中断当前工具吗？

不能，只在完整 Turn 后进入下一轮。立即停止应使用 cancel。

### 5. resume 会恢复旧 Sandbox 吗？

不会，只恢复脱敏上下文，创建新 taskId 和 sandboxId。

### 6. Plugin 是不是天然安全？

不是。当前安全来自只加载内置代码、窄 PluginContext 和后端重复授权。

### 7. Prompt Injection 被解决了吗？

没有被消除；系统通过最小能力和多层边界把副作用限制住。

### 8. Replay 是不是假 Agent？

它替换模型决策，但保留 Runtime、Policy、真实工具和 gVisor，用于确定性回归；不能替代 Live 能力评估。

### 9. 为什么文件写不需要 Operator 审批？

它只写 task 专属草稿区，不影响外部系统。真正的远端写操作仍应走专用 Plan。

### 10. 生产化最先补什么？

身份归属与凭据系统、持久化任务/审计、分布式 Worker、Connector 级授权和系统化 Eval，而不是先增加更多工具。

## 23. 自测标准

能脱离项目回答以下问题：

1. Agent 和 Workflow 的边界在哪里？
2. Tool Schema、参数校验和授权有何不同？
3. Context 裁剪为什么不能拆 Tool 协议组？
4. Session、Memory、RAG 有什么区别？
5. Prompt Injection 为什么不能只靠 Prompt 防？
6. cancel 为什么不能回滚外部动作？
7. MCP 和 Plugin 为什么不能代替权限系统？
8. Replay、Live 和 Eval 分别证明什么？

每个回答都至少包含：定义、项目选择、代价和生产化方向。
