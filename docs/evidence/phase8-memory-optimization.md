# Phase 8：P0/P1 记忆优化验收记录

日期：2026-09-29。此页只记录本地可复现结果；原始 JSON 与代码在本 PR 中。外部模型调用数为 **0**，没有追加使用旧付费预算。

## 执行范围

- `WorkingMemory` 从所选 Session 节点父链投影。工具调用和结果完整结束后 checkpoint；半轮记录不成为恢复叶子。模型假设保留 `model-proposed` 标识，证据 ID 只验证来源，不验证语义。
- 当前状态观测 60 秒后标为过期；日志事件单列事件时间与查询窗口。若无法明确关联告警的集群与资源身份，标为 `unverified`，不宣称属于该资源。
- 每次模型调用组装有界视图，不修改原始 JSONL。旧自动注入的记忆只在视图中去重；长期记忆按作用域过滤，旧无作用域条目需显式读取。

## 无费用验证

| 检查 | 实测结果 | 边界 |
|---|---|---|
| `python -m unittest discover -s agentd/tests -v` | 90/90，通过；耗时 279.476 秒 | [原始输出](phase8-python-tests.txt) |
| 工作记忆与审查边界定向回归 | 15/15，通过；耗时 0.085 秒 | 包括最终待验证标识；[原始输出](phase8-memory-boundary-tests.txt) |
| `python -m agentd.memory_eval` | 2/2 事实，Precision/Recall 均 1.0；冲突 1，注入缺席 | 合成显式标记，不是自然语言抽取质量；[原始结果](phase8-memory-eval.json) |
| `python -m agentd.memory_context_eval` | 116 条合成消息，源文本 92,400 字符；旧视图 47,705 字符，新视图 25,135 字符，估算总量 32,231/32,768 token | 新视图保留早期证据指针、待检查项和最近拒绝；估算不等于精确 tokenizer；[原始结果](phase8-memory-context-replay.json) |
| `python -m agentd.ops_demo` | 3/3 联合回放通过 | 实际本地 OpenSearch/Milvus，脚本决策、Fake Sandbox、Fake 路由；没有真实 LLM 或生产集群；[原始结果](phase8-joint-replay.json) |

长会话固定输入的已发生重复检查数为 0；不能从这个数推断真实模型是否会减少重复工具调用。旧视图丢失早期证据和最近拒绝，新视图保留指针及拒绝。这是上下文可见性的对照，不是回答质量测评。

单元测试覆盖兄弟分支隔离、从中间完整轮次分支、半轮取消后恢复、工具配对、证据 ID 拒绝、过期状态、身份匹配、上下文预算、旧无作用域记忆显式读取、跨集群隔离、同作用域冲突及遗忘后重建、Agent Token 只读接口。另覆盖真实 sandboxd stdout 形状提取 UID、旧 Prometheus 样本的最终输出降级、超大更新明确拒绝、省略字段保留待办、恢复后的系统规则及历史拒绝。真实模型能否主动维护优质假设、引用是否语义正确，以及实时集群身份映射，仍需单独测评。

复现命令见 [学习文档](../42-运维Agent工作记忆与上下文压缩.md)。
