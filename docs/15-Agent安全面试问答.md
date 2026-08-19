# Agent 安全面试问答

> 回答顺序建议：先说项目事实，再解释原理，最后说明非生产边界。不要把 Replay 说成 Live，不要把 gVisor 说成绝对安全。

## 1. 30 秒项目介绍

“我在已有 Go sandboxd 上增加了一个 Python LangGraph 告警诊断 Agent。真实 Prometheus/Alertmanager 把外部告警送进 Agent，Agent 查询 Prometheus，再通过 Go 结构化接口在 gVisor 沙箱里只读 Kubernetes。Pod Log 和 ConfigMap 中的间接 Prompt Injection 都真实进入上下文。DeepSeek Live 三次均完成诊断且未服从注入，其中一次完整脚本通过；Replay 则确定性触发危险调用。危险 operation 分别被 Python Policy、Go Tool Policy 和 Kubernetes RBAC 拒绝；允许的 scale 只能形成 Pending DryRun Plan，Agent Token 无法批准。”

## 2. 架构与选型

### Q1：为什么选择 LangGraph，不直接用 LangChain Agent？

LangChain 高层 Agent 上手快，但循环、状态和默认行为更隐式。本项目要讲清最大轮数、工具计数、拒绝回灌和最终结构校验，所以只用 LangGraph `StateGraph` 手写五个节点。不是否定 LangChain，而是当前面试 Demo 更需要可解释控制流。

### Q2：为什么 Agent 用 Python，沙箱仍用 Go？

Python 的模型 SDK 与 Tool Calling 生态成熟，适合快速实现 Agent；Go 的 client-go、并发控制和服务端边界更适合 Kubernetes。更重要的是不把可信执行层重写：Python 可以失败或被绕过，Go 白名单和 RBAC 仍成立。

### Q3：这是不是 ReAct？

是最小 ReAct/Tool Calling 循环：模型根据告警和上一轮 Observation 选择 Action，工具结果再回灌，直到输出 Final。项目不记录隐藏思维，只记录 Tool Call、Observation、拒绝层和结构化结论。

### Q4：为什么不用多 Agent、RAG 或长期记忆？

当前任务是一条短告警诊断链，工具只有三个，权威信息来自实时 API。多 Agent 增加身份与并发边界；RAG 增加新的不可信内容面；长期记忆增加污染和隐私问题。它们的学习成本和实现成本都没有强化当前主线。

### Q5：为什么不直接用 Kubernetes Python Client？

模型侧不应该拥有 kubeconfig 或通用 Kubernetes Client。所有 K8s 请求都经过 Go 的固定 operation、固定 namespace 和固定 URL 构造，并在 gVisor 内使用短时 projected Token；这样可信边界集中且能复用 Phase 1。

## 3. Tool Calling 与状态

### Q6：模型返回 Tool Call 就会执行吗？

不会。Tool Call 只是候选意图。`validate_tools` 会检查工具名、字段集合、namespace、DNS 名称、tailLines、replicas 和调用次数；被拒绝的调用只得到 `ToolMessage`，不会进入 Dispatcher。

### Q7：为什么拒绝后也要返回 ToolMessage？

OpenAI Tool Calling 协议要求每个模型产生的 `tool_call_id` 有对应 ToolMessage。直接丢弃会让下一轮消息历史不完整，Provider 可能拒绝请求。安全拒绝也必须保持协议正确。

### Q8：怎样避免 Agent 无限循环？

模型轮数 6、工具次数 8、Prometheus 查询 4、总任务 120 秒；LangGraph 还有 recursion limit。任何一层达到上限都会安全停止并输出可审计结果。

### Q9：Alertmanager 为什么需要去重？

Webhook 可能重试，同一 firing 告警可能至少投递一次而不是恰好一次。Demo 用 fingerprint 做 10 分钟内存去重。生产环境需要数据库幂等键，否则进程重启会丢失去重状态。

### Q10：Task、Trace 和会话身份是一回事吗？

不是。Task 是一次告警处理，Trace 是该 Task 的审计记录；当前没有多租户会话身份。API Token 持有者可读取所有 Task，这是明确的单租户 Demo 边界。生产实现要给 Task 绑定 tenant、subject 和权限检查。

## 4. Prompt Injection 与安全边界

### Q11：System Prompt 能防 Prompt Injection 吗？

只能降低概率，不能构成授权边界。日志、ConfigMap 和告警注解都可能伪装成系统指令。项目的安全结论来自代码白名单、隔离、网络、RBAC 和审批，而不是模型一定听话。

### Q12：如何证明注入真的进入上下文？

不看最终文本自报。Trace 必须满足 `injectedVia=["podlog"]`，对应 `get_pod_logs` Observation 中出现注入标记。第一次 previous log 没拿到业务内容时脚本就失败，修复后才得到证据。

### Q13：为什么要有 Python 和 Go 两层工具策略？

Python Policy 提供早拒绝、少开销和清晰 Observation；Go Policy 防止调用方绕过 Agent。只在客户端检查等于把安全交给不可信客户端，不是服务端授权。

### Q14：RBAC 已经只读，为什么还需要 Tool Policy？

RBAC 是最后一道集群权限边界，但它不知道业务上哪些读操作、namespace 和参数适合模型。Tool Policy 缩小能力面、减少数据暴露，也能在请求进入沙箱前拒绝明显危险操作。两层解决不同问题。

### Q15：gVisor 在这里防什么？

它隔离承载诊断命令的 Pod，减少系统调用直接到宿主内核的攻击面。它不负责模型授权，也不替代非 root、seccomp、capability、NetworkPolicy、RBAC 或资源限制。

### Q16：为什么通用 Exec 还保留？

Phase 1 的通用 Exec 是沙箱能力和 RBAC 兜底验证入口；Agent 正常路径不使用它，而使用结构化 Diagnostic API。真实拒绝矩阵故意通过 Exec 发 DELETE，证明即使更上层边界失效，RBAC 仍拒绝。生产 Agent API 可以完全不暴露通用 Exec。

### Q17：Prompt Injection 没触发危险调用，安全实验算成功吗？

Live 下可以算 `not-triggered`，说明这次模型没受诱导，但不能证明执行边界。Replay 确定性触发危险调用，用于验证边界；Live 用于验证真实模型能完成诊断。两种证据回答不同问题。

## 5. Kubernetes 与审批

### Q18：Agent 查询 Kubernetes 的身份是什么？

诊断命令运行在 `sandboxd-demo` 的 gVisor Pod 中，使用 projected ServiceAccount Token 和 CA。ClusterRole 允许 Pod/Log/ConfigMap/Event/Deployment 只读，不允许 Secret、写操作或 pods/exec。

### Q19：为什么允许生成 scale-to-zero Plan？

CrashLoop 时暂停副本是一个可讲解的候选缓解动作，但 Agent 只提交意图。Go 服务限制 namespace、Deployment、replicas 0–10，做 server-side DryRun，Plan 保持 pending。是否执行由独立 Operator 决定。

### Q20：Agent 为什么不能自己 approve？

Agentd 根本没有 Operator Token；Agent Token 调 approve 返回 401。职责分离不能只靠提示词，必须让低信任进程在凭证上做不到。

### Q21：DryRun 成功是不是一定能执行？

不是。DryRun 不持久化、不锁资源，也不是事务。Approve 前还要比较 UID/resourceVersion，Update 时依赖乐观锁；对象变化就 stale，要求重新审核。

## 6. 可观测性、证据与工程取舍

### Q22：`vector(1)` 算真实告警吗？

它是真实 Prometheus 规则计算、Alertmanager 路由和 Webhook 传输，但不是对 CrashLoop 状态的真实检测。项目明确称它为确定性触发 Fixture；真实故障由 gVisor CrashLoop Deployment 单独证明。没有引入 kube-state-metrics 是为了控制 Demo 成本。

### Q23：为什么 PodList 要压缩？

原始 PodList 很容易超过模型 Observation 上限，简单截断会破坏 JSON，还会携带大量无关数据。Go 可信侧只保留 name、phase、restartCount，降低 Token、解析和注入风险。

### Q24：HTTP 200 为什么还可能是失败？

第一次 `previous=true` 返回 HTTP 200、curl exit 0，但 stdout 是 unable to retrieve container logs，业务语义不满足。端到端测试必须断言日志标记进入 Trace，不能只断言状态码。

### Q25：Trace 为什么不保存 Chain of Thought？

系统只需要审计输入、Tool Call、参数、Observation、拒绝层、最终 Diagnosis 和耗时。隐藏思维既不稳定也可能含敏感信息，不应作为安全证据或日志要求。

### Q26：怎样防凭证进入 Git 和 Trace？

Token 每次运行随机生成，只通过环境或 `.cache` 下临时 Alertmanager 配置传递；配置退出时删除。Agentd 不记录 Header/环境变量，Operator Token 不传给 Agentd，提交前再扫描 staged diff。

### Q27：为什么测试不多？

这是秋招 Demo，不追求覆盖率。测试只锁高风险不变量：Go operation 白名单和参数、PodList 压缩、API Token 隔离、Replay 危险动作拒绝、sandbox finally release。其余价值由真实低资源 E2E 证明。

## 7. 扩展与项目边界

### Q28：怎样接入外部生产 Prometheus？

把 Connector 的 base URL、TLS 和认证放在可信配置中，模型仍只提供受限 PromQL。还要加 tenant 映射、查询 allowlist/成本限制、审计和网络出口策略。当前 localhost Prometheus 是外部于 K8s 的真实进程，但不是生产系统。

### Q29：怎样运维外部 Kubernetes 集群？

为每个 target 注册独立 Connector 与最小身份，Task 绑定 target ID，模型不能提交任意 endpoint 或 kubeconfig。不同 target 的凭证、网络和审计必须隔离。当前项目只诊断自己的 kind，不能宣传成通用多集群运维。

### Q30：怎样做多会话、多沙箱身份？

为每个 Task 建立 owner/tenant/target，认领 sandbox 时写不可变绑定；所有 read/release/plan API 检查 subject 与绑定一致；SA、namespace、NetworkPolicy 和配额按租户或信任域隔离；Trace 也按 tenant 授权。当前单 Token 没有这层能力。

### Q31：怎样支持文件进出？

优先设计有大小、类型、路径、哈希和生命周期限制的 artifact API，对象存储使用短时签名 URL；沙箱内只挂任务专属目录。不要直接给模型任意 tar 路径或宿主挂载。该功能当前未实现。

### Q32：如果生产化，优先补什么？

优先顺序是身份归属和专用 ServiceAccount、TLS/Secret 轮转、持久化幂等 Task/Plan/审计、按 target 的 Connector 与网络策略、限流/配额、HA 与恢复。不是先加更多 Agent 或更复杂 Prompt。

## 8. 反问式追问准备

### “既然 Replay 是脚本，不就是假的 Agent 吗？”

Replay 只替换模型决策，LangGraph、Policy、工具、Prometheus、Alertmanager、Go、gVisor、Kubernetes、RBAC 和 Plan 都是真实运行。它证明执行边界可复现，不证明真实模型能力；所以还保留单独的 Live 完成标准。

### “Go Tool Policy 已经够了，Python Policy 是重复代码吗？”

这是纵深防御和职责不同：Python 给模型快速、结构化拒绝并减少无效请求；Go 防绕过。两层规则确实要保持一致，因此 operation 集合很小且有测试；若生产规模扩大，可以从同一策略定义生成两侧 Schema。

### “你这个项目最大的不足是什么？”

当前是单节点、单租户、内存 Task/Plan、简化 Bearer Token，且只诊断本地 kind。DeepSeek Live 虽已实测，但三次工具选择并不完全相同，说明模型行为有概率性。它适合证明机制和面试讲解，不适合直接接生产系统。

## 9. 一分钟展开模板

1. 入口：真实 Alertmanager Webhook。
2. Agent：LangGraph 显式有限循环。
3. 工具：Prometheus、gVisor K8s read、Pending Plan。
4. 攻击：Pod Log 间接 Prompt Injection。
5. 边界：Agent Policy、Go Policy、RBAC、审批。
6. 证据：Starting gVisor、403/401、replicas 不变、清理无残留。
7. 边界声明：Replay 与 Live 分开验证；Live 注入 0/3 触发；单租户 Demo。

## 10. 自测标准

不看文档，能回答以下问题才算掌握：

- 画出完整数据流和信任边界；
- 指出每个 Token 能访问哪些端点；
- 解释 Replay 替换了什么、没替换什么；
- 从 Trace 找到注入来源和拒绝层；
- 解释为何 Plan pending 但 Deployment 不变；
- 说出三个生产化优先项和三个明确非目标。
