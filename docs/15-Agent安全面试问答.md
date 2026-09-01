# Phase 2 Agent 安全问题索引

> **文档职责：只保留问题和权威出处，不再维护完整答案。** 当前项目级回答统一查 [10 项目设计 FAQ](10-面试问答与项目讲法.md)；Phase 2 LangGraph 的历史事实查 [14 历史实现与证据](14-Phase2-LangGraph历史实现与证据.md)；实际运行结果查 [Phase 8 evidence](evidence/phase8-agent-alert.md)。这样可以避免当前 Runtime 与历史表述互相覆盖。

## 1. 使用方法

1. 先遮住“核对出处”，独立口述答案；
2. 先用 [24 项目全景](24-项目全景与心智模型.md) 核对当前边界；
3. 再到 `10` 核对项目答案，不从本文件背第二个版本；
4. 只有问题明确追问 Phase 2 时，才读取 `14` 和 Phase 8 证据。

历史口径必须说“Phase 2 当时使用 LangGraph”；当前口径必须说“main 已改为手写 Pi-style 双层循环”。

## 2. 架构、循环与 Tool Calling

| 问题 | 核对出处 |
|---|---|
| Phase 2 为什么选择 LangGraph，而不是 LangChain 高层 Agent？ | [14 §2.1](14-Phase2-LangGraph历史实现与证据.md#21-为什么使用-python--langgraph)；当前选型对比见 [10 Q20](10-面试问答与项目讲法.md#q20为什么从-langgraph-改成手写循环) |
| 为什么 Agent 用 Python，可信 Kubernetes 边界继续用 Go？ | [24 §2](24-项目全景与心智模型.md#2-四层架构)、[25 代码地图](25-代码导读与模块地图.md) |
| 这是不是 ReAct？Tool Calling 与 ReAct 有何区别？ | [10 Q19](10-面试问答与项目讲法.md#q19这是不是-react)、[26 §2](26-Agent八股知识地图.md#2-react-和-tool-calling) |
| 模型返回 Tool Call 后为什么不能直接执行？ | [10 §8](10-面试问答与项目讲法.md#8-agent-安全与工具问答)、[14 §2.2](14-Phase2-LangGraph历史实现与证据.md#22-三个工具) |
| 被拒绝的 Tool Call 为什么仍要闭合 Tool Result？ | [14 §12](14-Phase2-LangGraph历史实现与证据.md#12-高频自测问题)、[11 开发坑](11-开发踩坑与排障.md) |
| 如何限制无限循环、工具风暴和上下文膨胀？ | [14 §2.3](14-Phase2-LangGraph历史实现与证据.md#23-有限状态)、[26 §11](26-Agent八股知识地图.md#11-context-engineering) |
| Alertmanager 为什么需要去重，当前去重边界是什么？ | [14 §3](14-Phase2-LangGraph历史实现与证据.md#3-一条告警如何运行)、[14 §9](14-Phase2-LangGraph历史实现与证据.md#9-必须掌握的八股知识) |

## 3. Prompt Injection 与纵深防御

| 问题 | 核对出处 |
|---|---|
| System Prompt 能防 Prompt Injection 吗？ | [10 Q26](10-面试问答与项目讲法.md#q26prompt-injection-被解决了吗)、[26 §13](26-Agent八股知识地图.md#13-prompt-injection) |
| 如何证明注入文本真的进入模型上下文？ | [14 §4](14-Phase2-LangGraph历史实现与证据.md#4-为什么-observation-也是不可信输入)、[Phase 8 evidence](evidence/phase8-agent-alert.md) |
| Python Policy、Go Tool Policy 和 RBAC 为什么不是重复保护？ | [24 §8](24-项目全景与心智模型.md#8-安全的核心不是-prompt)、[14 §5](14-Phase2-LangGraph历史实现与证据.md#5-纵深防御怎样协作) |
| gVisor 防什么，又不防什么？ | [10 Q1–Q3](10-面试问答与项目讲法.md#4-容器与-kubernetes-高频问答)、[01 gVisor](01-gVisor与容器隔离.md) |
| 为什么保留通用 Exec，它会不会成为 Agent 正常路径？ | [14 §5](14-Phase2-LangGraph历史实现与证据.md#5-纵深防御怎样协作)、[25 代码导读](25-代码导读与模块地图.md) |
| Live 没触发危险调用，能否证明系统安全？ | [10 Q27](10-面试问答与项目讲法.md#q27replay-与-live-分别证明什么)、[14 §7](14-Phase2-LangGraph历史实现与证据.md#7-live-与-replay-的证据边界) |

## 4. Kubernetes 身份、Plan 与审批

| 问题 | 核对出处 |
|---|---|
| Agent 查询 Kubernetes 时使用什么身份？ | [24 §3](24-项目全景与心智模型.md#3-一条告警从头到尾怎样流动)、[03 RBAC](03-ServiceAccount与RBAC.md) |
| 为什么 Agent 可以提议 scale Plan，却不能直接执行？ | [24 §4](24-项目全景与心智模型.md#4-读路径和写路径为什么必须分开)、[10 §6](10-面试问答与项目讲法.md#6-dryrun-与审批问答) |
| Agent 为什么不能 approve？401 与 403 如何解释？ | [10 Q18](10-面试问答与项目讲法.md#q18agent-approve-为什么返回-401)、[08 审批门](08-DryRun与审批门.md) |
| DryRun 成功为什么不保证未来能执行？ | [10 Q15–Q17](10-面试问答与项目讲法.md#6-dryrun-与审批问答) |

## 5. 证据、审计与生产边界

| 问题 | 核对出处 |
|---|---|
| `vector(1)` 证明了什么，没有证明什么？ | [Phase 8 evidence](evidence/phase8-agent-alert.md)、[14 §7](14-Phase2-LangGraph历史实现与证据.md#7-live-与-replay-的证据边界) |
| 为什么 PodList 要在 Go 可信侧压缩？ | [14 §6](14-Phase2-LangGraph历史实现与证据.md#6-podlist-为什么在-go-端压缩) |
| HTTP 200 为什么仍可能是业务失败？ | [11 坑 36](11-开发踩坑与排障.md#36-gvisor-下-previous-log-返回-http-成功也可能没有业务日志) |
| Trace 为什么不保存隐藏思维？ | [26 §19](26-Agent八股知识地图.md#19-agent-可观测性) |
| Replay、Live、Mock、Eval 分别证明什么？ | [26 §18](26-Agent八股知识地图.md#18-livereplaymock-和-eval)、[10 Q27](10-面试问答与项目讲法.md#q27replay-与-live-分别证明什么) |
| 当前单租户 Demo 离生产系统还差什么？ | [10 §10](10-面试问答与项目讲法.md#10-生产化差距)、[24 §9](24-项目全景与心智模型.md#9-当前做到了什么没有做到什么) |

## 6. 自测标准

不看答案完成以下四项：

1. 分别用当前口径和 Phase 2 历史口径画控制流；
2. 解释 Replay 替换了哪个组件、保留了哪些真实边界；
3. 指出一次危险调用依次可能在哪些层被拒绝；
4. 给每个结论指出代码位置或 evidence，而不是引用本问题索引。
