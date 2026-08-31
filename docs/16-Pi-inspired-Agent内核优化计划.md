# Phase 2.1 Pi-inspired Agent 内核优化计划

> 本文是 Phase 2.1 的目标锚点。Pi 只作为 Agent 内核设计参考；实现仍使用 Python、LangGraph 和现有三个固定工具。

## 1. 目标与边界

在不改变 Phase 2 告警诊断链路和 Go 可信执行边界的前提下，补齐四个适合秋招讲解的工程能力：工具结果双通道、生命周期可观测性、显式上下文治理、取消与资源释放闭环。

保留：单告警单任务、单 Worker、顺序工具、DeepSeek Live/确定性 Replay、Prometheus、sandboxd、gVisor、RBAC、Pending Plan。

不做：安装或依赖 Pi、TypeScript 重写、多 Provider 大重构、会话树、长期记忆、多会话、多 Agent、动态插件、任意 Bash/文件工具、工具并行、外部 Trace 平台和生产级事件总线。

## 2. 最小数据模型

~~~python
class ToolResult:
    model_content: str       # 进入 ToolMessage 的有界 JSON 摘要
    audit_details: dict      # 脱敏且有界的结构化执行证据
    is_error: bool
    denied: bool
    deny_layer: str

class AgentEvent:
    index: int
    type: str                # agent/turn/model/tool/sandbox 生命周期
    iteration: int | None
    tool: str | None
    elapsed_ms: int
    details: dict            # 不含 Token、Header、隐藏思维和原始大响应

class ModelResult:
    message: AIMessage
    usage: ModelUsage
    finish_reason: str
    elapsed_ms: int
~~~

模型只看到 `model_content`；Trace 保存 `audit_details`。Diagnosis 的 evidence、deniedActions、planId 继续只从可信 Graph State 写入，不能相信模型自报。

## 3. 生命周期事件

固定事件集合：

~~~text
agent.started
sandbox.claim.started / sandbox.claim.completed
turn.started / model.completed / turn.completed
tool.started / tool.completed / tool.denied / tool.failed
context.transformed
agent.completed / agent.timed_out / agent.cancelled / agent.failed
sandbox.release.started / sandbox.release.completed / sandbox.release.failed
~~~

本期事件只是每任务内存列表并最终写入脱敏 Trace，不建设消息总线。模型事件记录 provider、model、finishReason、inputTokens、outputTokens 和 elapsedMs；Replay 对无法提供的字段填 0 或 `replay`，不得伪造成本。

## 4. 上下文转换

模型调用前统一执行 `transform_model_context(messages)`：

1. 第一条 SystemMessage 必须存在且完整保留；
2. 保留初始 Alert HumanMessage；
3. 保留最近的 Assistant/Tool 协议消息，不能产生孤立 Tool Call；
4. 单条 ToolMessage 继续受 4 KiB 限制，总上下文再受一个保守字符预算；
5. 发生裁剪时写 `context.transformed`，只记录裁剪前后字符数和消息数；
6. 不做 LLM 摘要，不引入第二次模型调用，避免摘要注入和额外成本。

由于每任务最多 6 轮、8 个工具，本期采用确定性裁剪，不实现长会话自动 Compaction。

## 5. 取消与清理

- `asyncio.timeout`/`wait_for` 控制 120 秒任务上限；超时停止 Graph 后续节点；
- Worker 被关闭时取消当前 Task，不吞掉 `CancelledError`；
- 已认领 sandbox 始终在 `finally` 中释放；
- 释放使用独立、短时的 cleanup task，避免父任务已取消后跳过清理；
- release 失败写入事件但不能包含认证信息；
- 不增加用户取消 API，本期只验证超时和进程关闭取消语义。

## 6. 文件改动

~~~text
agentd/app/models.py          ToolResult、AgentEvent、ModelUsage、Trace 字段
agentd/app/model_gateway.py   ModelResult、usage/finishReason、provider 元数据
agentd/app/context.py         确定性上下文转换与预算
agentd/app/graph.py           事件记录、双通道、取消和独立清理
agentd/tests/                 双通道、上下文、事件、取消最小测试
docs/14、docs/15、docs/11    学习、面试与踩坑
README.md / agentd/README.md  能力与边界
~~~

允许为保持简单合并过小类型，但禁止增加没有调用方的抽象层。

## 7. 实现顺序

1. 数据模型和 Replay/Live Gateway 统一返回模型元数据；
2. 工具 Dispatcher 产生双通道结果，Graph 只把 model_content 回灌模型；
3. 增加生命周期事件并兼容旧 `steps` 字段；
4. 增加确定性上下文转换；
5. 增加取消、超时和独立 release；
6. 最小单测、确定性 Replay、文档与 GitHub 审计。

## 8. 最小完成证据

- 原 Replay 告警链路行为不变：注入被拒绝、Pending Plan、沙箱释放；
- 单测证明模型上下文不包含 audit-only 字段；
- 单测证明安全 System Prompt 不会被裁剪，Tool Call/Tool Result 协议不被拆散；
- Trace 包含 sandbox、turn、model、tool、agent 事件以及模型耗时/usage/finishReason；
- 单测证明取消或超时后只释放一次 sandbox；
- Live 不强制重跑；真实集群只在资源检查通过且 Replay 单测通过后执行一次低资源验证；
- Go test/vet/build、Python compileall/unittest 通过；秘密扫描无命中；
- 文档、代码、PROGRESS 小步提交并推送当前 GitHub 分支。

## 9. 面试一句话

“我参考通用 Agent Runtime 的消息转换、事件流和工具结果分层思想，但没有引入一个更重的框架：LLM 只接收有界摘要，审计保留结构化证据；每轮模型和工具都有生命周期事件；上下文做确定性预算；任务取消后使用独立清理窗口释放 gVisor 沙箱，最终安全边界仍是 Go Policy、RBAC 和审批门。”
