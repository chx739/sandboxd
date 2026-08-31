# Pi-style 运维 Agent 面试问答

> Phase 3 自测手册。回答顺序固定为：项目事实 → 原理 → 当前边界。不要把“借鉴 Pi”说成“复刻 Pi”，不要把插件清单说成授权系统。

## 1. 30 秒介绍

“我在 Go sandboxd/gVisor 安全执行层之外，实现了一个受 Pi 设计启发的极简 Python 运维 Agent Runtime。它用手写双层循环处理 Tool Calling、steer 和 follow-up，用 append-only JSONL 保存线性事故 Session，用静态受信任 Registry 插件化 Prometheus 和 Kubernetes/Plan 工具。Agent 没有宿主 Shell、kubeconfig 或 Operator Token；危险动作仍由 Python Policy、Go Tool Policy、gVisor、NetworkPolicy、RBAC 和独立审批门限制。项目重点是用简单代码讲清 Agent 灵活性与执行安全边界如何分离。”

## 2. 架构与选型

### Q1：为什么从 LangGraph 改成手写循环？

LangGraph 已经跑通，但这个 Demo 的循环只有模型、校验、工具和结束几个状态。手写两个 while 后，steer/follow-up 的消费时机、预算和取消路径都能直接从代码读出，还删除了一个运行时依赖。代价是复杂分支、持久化 checkpoint 和图可视化要自己实现；本项目范围小，收益大于成本。

### Q2：为什么不是直接使用 Pi？

Pi 是 Coding Agent，默认工具和扩展运行在宿主进程权限下，并带完整 Session/UI/扩展生态。本项目需要 Python、运维工具和已有 Go sandboxd 边界；直接接入会增加 Node/TypeScript、宿主工具和供应链面。这里只提炼 Transcript、双层循环、控制队列、Session 和 Extension 分层。

### Q3：这还是 ReAct 吗？

是最小 Tool-augmented ReAct：模型提出 Action，工具返回 Observation，再循环到 Final。项目不保存隐藏思维，只审计模型可见消息、工具调用、结果和拒绝层。

### Q4：为什么 Agent 用 Python，安全执行层用 Go？

Python 适合模型 SDK、Tool Calling 和快速迭代；Go 的 client-go、并发控制和结构化服务端校验已被真实验证。可信边界不应为了统一语言重写。

### Q5：底层 sandboxd 为什么完全不用改？

Phase 3 只改变“怎样组织模型、消息和工具”。插件最终仍调用原有结构化接口，Go 继续验证 operation、namespace 和参数；gVisor/RBAC/审批语义没有变化。Agent 内核可替换，执行边界保持稳定，正说明分层有效。

## 3. 循环与交互控制

### Q6：为什么是双层循环？

内层表示一个正在进行的 Agent 任务：model → tool → steer → model。外层表示任务本来结束后还有 follow-up。两类消息语义不同，分层后停止条件和 Trace 更清楚。

### Q7：一个 Turn 到底是什么？

一次模型调用，加上这次响应产生的一组顺序工具执行。Turn 结束才读取 steer，避免把新指令插进半个 Tool Call 协议。

### Q8：steer 和 follow-up 有什么区别？

steer 改当前任务下一轮方向，例如“只读，不要提 Plan”；follow-up 在当前答案自然结束后追加需求，例如“再给交接摘要”。它们分别有独立 FIFO。

### Q9：steer 能阻止正在执行的危险动作吗？

不能。它只在 Turn 安全点生效，不能撤回已发出的外部请求。危险动作必须在执行前由 Policy/审批门拒绝；需要停止当前运行时使用 cancel。

### Q10：cancel 为什么不是一条特殊消息？

消息要等循环安全点才消费，不能及时停止 Provider 或工具。cancel 直接取消运行中的 asyncio Task，让超时、用户停止和资源清理共用真实取消语义。

### Q11：`finally` 里 release 为什么还需要独立 Task？

协程取消会在 await 处传播，复用同一条已取消执行链可能让清理也中断。Runner 为 release 建独立 Task 并给有限窗口，再用测试验证一个已认领 Sandbox 只释放一次。

### Q12：为什么工具不并行？

顺序执行让预算、证据、拒绝和 Plan 顺序确定，也更易讲清 steer 的安全点。并行可降低无依赖查询延迟，但会引入取消、竞态和 Trace 合并成本，不符合当前秋招 Demo 的性价比。

## 4. Session 与身份

### Q13：taskId、sessionId、sandboxId 有什么区别？

taskId 是一次运行；sessionId 是跨运行的线性事故上下文；sandboxId 是这次运行临时认领的执行环境。resume 保留 sessionId，但创建新 taskId 和 sandboxId。

### Q14：为什么 Session 用 JSONL，不用数据库？

单进程 Demo 只需要追加写、可读和最小恢复。JSONL 一行一个完整事件，进程中断最多损失最后一行，代码很少。它不支持事务、索引、HA 或多进程一致性，生产化必须换持久化存储。

### Q15：resume 恢复了什么？

恢复最后一个完整、已脱敏的公开 Transcript 和原始告警，再追加用户续作消息。它不恢复旧 TCP 连接、旧进程、半个 Tool Call 或旧 Sandbox，所以属于语义恢复。

### Q16：Session 为什么不保存 Chain of Thought？

隐藏思维不稳定、可能含敏感信息，也不是授权或事实证据。恢复只需要公开消息、Tool Call 和 Observation；审计事实来自 Trace 和真实工具状态。

### Q17：Session 文件设 0600 就一定安全吗？

在原生 Linux 文件系统上才有这个前提。WSL 的 `/mnt/c` 若未启用 DrvFS metadata，chmod 可能仍显示 0777。因此运行状态应放 Linux 文件系统；生产还要加加密、租户授权、备份和保留策略。

### Q18：这算多会话、多沙箱了吗？

算最小的多 Task/线性多 Session 身份模型，但不是生产多租户。当前单 Worker 串行运行，API Token 持有者可控制所有 Task；没有 owner/tenant/target 级授权。

## 5. 插件与安全

### Q19：插件化带来了什么？

Tool Schema、Handler、说明和 capability 被放到一个模块中，Runtime 不需要知道 Prometheus/Kubernetes 细节。新增受限 Connector 时改插件注册，不改 Agent Loop。

### Q20：为什么不支持动态插件？

动态插件本质是向高权限进程加载第三方代码，会带来签名、依赖、版本、凭据、网络和宿主权限问题。Demo 用代码显式注册两个受信任插件，牺牲热插拔换取小而清楚的信任面。

### Q21：Plugin Manifest 是权限吗？

不是。Manifest/capability 是发现和审计信息，Tool Schema 是模型协议。真正授权来自 Python Policy、Go 服务端白名单、RBAC、NetworkPolicy、gVisor 和 Operator 凭据分离。

### Q22：插件为什么不能直接拿 kubeconfig 或 SSH 私钥？

那会把模型插件变成通用高权限执行器。一旦参数校验或 Prompt 出问题，攻击面就是整个目标系统。正确方式是 Connector 持有受限身份，只暴露固定 operation 和固定 target ID。

### Q23：如何扩展成通用运维 Agent？

先建立 Target Registry：每个 target 绑定 Connector、最小凭据、网络出口和 owner；Task 只提交 targetId，不能提交任意 endpoint。然后逐个增加只读 Prometheus、Kubernetes、Linux Host Connector，最后才考虑带审批的写动作。

### Q24：Prompt Injection 现在被解决了吗？

没有从语言层“解决”。日志、告警和 ConfigMap 仍是不可信输入。系统保证即使模型提出越权调用，也在独立执行边界被拒绝；Replay 确定性验证危险动作，Live 只验证真实模型可用性。

### Q25：插件越多，安全边界会怎样变化？

能力并集会扩大，凭据和网络出口也更复杂。每个插件都要有固定输入 Schema、服务端校验、目标绑定、超时/输出上限、审计和最小身份；不能因为注册表统一就认为风险被统一解决。

## 6. 工程取舍与追问

### Q26：为什么两个内存 FIFO 不加锁？

FastAPI 路由和 Worker 在同一个 asyncio 事件循环，enqueue/drain 中间没有 await，不会线程并发修改。若改多线程或多进程，这个假设立即失效，必须换线程安全或共享队列。

### Q27：为什么 Session 保存 Transcript 快照，而不是每条消息一个事件？

Runner 在完整运行或取消清理点落一次快照，恢复逻辑最小，也避免半个 Tool Call 协议。代价是长会话会重复占空间；生产可改消息增量、snapshot + compaction。

### Q28：整个 Phase 3 的代码量和实现成本如何？

核心 Loop、Control、Plugin Registry、Session 和 Store 都是几百行量级，没有引入新框架。难点不在代码量，而在取消清理、消息协议、身份区分、脱敏和不破坏旧安全证据。

### Q29：最大的真实开发坑是什么？

一是把整段 Tool Call JSON 正则脱敏后再解析，替换或截断会破坏 JSON，最终改为逐叶脱敏；二是 WSL 的 Python TEMP 落在 `/mnt/c`，POSIX 权限测试显示 0777，最终固定到原生 `/tmp` 并明确运行目录边界。

### Q30：这个项目能直接上生产吗？

不能。它是单节点、单进程、单租户、内存队列、JSONL Session、简化 Bearer Token 的教学 Demo。生产至少要补 tenant/owner/target 授权、持久化队列与幂等、Connector 凭据隔离、TLS/轮转、审计保留、HA、限流和灾难恢复。

## 7. 一分钟展开模板

1. 入口：Alertmanager 或手工 Task。
2. 身份：taskId 是一次运行，sessionId 是事故上下文，sandboxId 是临时执行环境。
3. 循环：内层 Tool/steer，外层 follow-up，cancel 走真实协程取消。
4. 插件：静态受信任 Registry，只暴露 Prometheus 和 Kubernetes/Plan。
5. 安全：Agent/插件不等于授权，最终由 Go/gVisor/NetworkPolicy/RBAC/审批门约束。
6. Session：脱敏 append-only JSONL，resume 创建新 Task 和新 Sandbox。
7. 边界：不是完整 Pi，不是动态插件市场，也不是生产多租户系统。

## 8. 自测标准

不看文档完成以下四项：

- 画出双层循环，并指出 steer/follow-up 的消费点；
- 用一句话区分 taskId/sessionId/sandboxId/planId；
- 解释为什么 Plugin Manifest 和 Tool Schema 都不是权限；
- 说出 cancel、resume、WSL 文件权限三个真实坑和当前解法。
