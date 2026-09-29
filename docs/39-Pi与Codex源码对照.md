# Pi 会话树与 Codex 分层记忆：固定源码对照

核对日期：2026-09-29。本页是对当前 MVP 的设计对照，不把 Python 实现说成上游完整移植。

## 1. Pi Session

参考仓库 `earendil-works/pi`，固定 commit `11894012dd461232eb075bc890538b6866860a10`。

[session-manager.ts 固定源码](https://github.com/earendil-works/pi/blob/11894012dd461232eb075bc890538b6866860a10/packages/coding-agent/src/core/session-manager.ts)：

| 源码位置/符号 | 上游行为 | sandboxd 对应和差异 |
|---|---|---|
| SessionEntry / id、parentId | 追加节点构成树 | JSONL 的 session.node 与 session.head；保留兼容 transcript |
| buildSessionProjection / buildSessionContext，约 576 行 | 从叶子计算模型可见上下文，包含压缩等处理 | 只沿活动父链加载脱敏消息；已有 Runtime 做有界上下文，不实现 Pi 全部投影种类 |
| branch，1574 行 | 改叶子指针，后续追加成为该节点的子节点 | 仅允许完整 Turn 做分支点，避免半个工具组；恢复创建新 task/sandbox |
| branchWithSummary，1595 行附近 | 分支时可追加旧路径的摘要 | MVP 没有自动分支摘要；不把其他支线自动带入新分支 |
| createBranchedSession，1626 行附近 | 可把路径抽出成独立 Session 文件 | MVP 在同一会话树内分支，未实现导出新的完整 Pi 文件 |
| parseSessionEntryLine，616 行附近 | 对无效 JSON 行有跳过逻辑 | MVP 仅容忍截断末行；中间损坏明确报错，避免静默丢历史 |

演示和测试见 [33](33-树形Session与分支恢复学习手册.md)。树形存储不需要 LangGraph；数据库节点关系与运行图是不同职责。

## 2. Codex Memory

参考仓库 `openai/codex`，固定 commit `46fdd5ef39735f4159cdcf0ec5e85c10521494e5`。

**路径核对发现**：该提交的 memories README 仍写着旧 `codex-rs/core/src/memories/`，但该路径下 phase1/phase2 已不存在。实际写管线在 `codex-rs/memories/write/src/`。以下链接以读到的源代码为准，不能把 README 旧路径继续当成当前实现。

| 层 | 固定源码 | 实际职责 | MVP 简化 |
|---|---|---|---|
| 触发 | [start.rs](https://github.com/openai/codex/blob/46fdd5ef39735f4159cdcf0ec5e85c10521494e5/codex-rs/memories/write/src/start.rs) | feature/root/ephemeral/DB 等条件；后台先 phase1 再 phase2，含版本与额度处理 | 显式 CLI extract/rebuild；没有后台自动触发 |
| 会话抽取 | [phase1.rs](https://github.com/openai/codex/blob/46fdd5ef39735f4159cdcf0ec5e85c10521494e5/codex-rs/memories/write/src/phase1.rs) | 从状态库领取近期空闲 rollout 作业；模型提取、并发上限、lease/retry、保存 stage1 | 只从成功会话活动路径读取；显式 MEMORY 格式确定性提取到 stage_one JSON |
| 跨会话整理 | [phase2.rs](https://github.com/openai/codex/blob/46fdd5ef39735f4159cdcf0ec5e85c10521494e5/codex-rs/memories/write/src/phase2.rs) | 全局租约、筛选 stage1、同步文件、workspace diff、运行 consolidation agent、完成后更新状态 | 单进程、确定性按 key 合并、保留旧值；无后台子 Agent/租约/工作区 Git 差分 |
| 文件产物 | [storage.rs](https://github.com/openai/codex/blob/46fdd5ef39735f4159cdcf0ec5e85c10521494e5/codex-rs/memories/write/src/storage.rs) | 原始记忆与 rollout 摘要等文件同步 | raw_memories、rollout_summaries、MEMORY、memory_summary；文件名相似不代表完整语义一致 |
| 读取与来源 | [read/lib.rs](https://github.com/openai/codex/blob/46fdd5ef39735f4159cdcf0ec5e85c10521494e5/codex-rs/memories/read/src/lib.rs) | 读路径独立于写管线，引用和使用统计模块 | 启动加载有界摘要，需要时 read_memory；引用 sessionId/nodeId，作为普通不可信历史资料 |

上游 write/lib.rs 显式定义 stage1 并发上限等常量；MVP 没有照搬这些生产协调参数。Codex 源码中的模型角色、权限与专用 Prompt 也没有原样复制进项目。

## 3. 本项目的分层含义

1. **原始记录**：Session JSONL 是可追溯来源，工具调用与结果成组保存。
2. **单会话阶段产物**：stage_one JSON 保存候选事实、来源节点、观察时间；同时可生成 rollout 摘要。
3. **长期整理**：同 kind/key 按观察时间选当前值，历史冲突保留。不是自动判断哪条事实客观正确。
4. **加载预算**：短摘要先入上下文，详情由只读工具按需取；当前预算是字符/字节边界，不是精确 tokenizer 预算。
5. **遗忘/重建**：删除某会话的 stage_one 后重新整理，清除对应摘要；原始 Session 留作审计，不宣称全系统物理擦除。

这套记忆位于 **agentd 的 Agent Runtime 层**。Milvus 知识库和 OpenSearch 日志是外部证据检索层，不能用它们取代 Session/Memory 的持久化来源。

## 4. 面试时的边界

可以讲：我读过固定版本的上游核心文件，将会话树和两阶段记忆拆成能运行、能测试的 Python MVP。

不能讲：完整复刻了 Pi/Codex；已实现自动后台记忆管线；模型能可靠提取任意自然语言事实；向量检索就是长期记忆；本地确定性测试证明真实 LLM 没有幻觉。

会话分支隔离指父链 transcript 的构建。项目级长期记忆可跨会话共享，并不提供每个分支独立的记忆空间；重新提取某会话活动分支后需显式 rebuild 才更新已整理记忆。涉及分支特有假设时应检查记忆来源，不能把这个 MVP 说成全局信息隔离。
