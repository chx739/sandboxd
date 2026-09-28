# Agent 会话树、记忆、检索与路由：最小实现方案

> 2026-09-28 环境与代码审计，现作为 Phase 7 的持续方案。M1–M5 的无密钥模块实现、测评与联合 Replay 已完成；GitHub 交付和外部 Live 授权状态以 `PROGRESS` 为准。当前事实以代码及各模块 evidence 为准。概念学习见 [32 学习手册](32-Agent记忆检索与路由学习手册.md)。

## 1. 目标与边界

在现有 Python `agentd` 上增加四个可运行、可测评的最小闭环：

1. 参考 Pi 的 append-only JSONL Session 树，实现历史节点、分支、恢复。
2. 参考 Codex 的分阶段记忆，实现会话提取、跨会话整理、分层读取。
3. 同一语料写入 Milvus 稠密向量索引和 Elasticsearch 倒排索引；以 ES BM25 + Milvus dense 检索、RRF 融合、BGE Reranker 重排为主链路。Milvus 自带 BM25 可作为对照组。
4. 假定「Jev」指 TypeSafe Jev：在每个 task 开始前分类并选择现有模型网关中的下游模型，记录决策、延迟和费用；失败时使用配置的默认模型。

不新增任意 Shell、动态插件、自动审批或生产级分布式存储。检索结果和记忆均视为不可信输入；现有 Python Policy、Go sandboxd、gVisor、RBAC 与审批门仍负责授权。先做单进程、单用户、单项目 Demo；不声称多租户隔离或生产可用性。

## 2. 实施前的代码基线与缺口

下表是本阶段开始时的基线；当前实现与验收见第 9 节，不作为待办重复实施。

| 能力 | 现状 | 最小改造位置 |
|---|---|---|
| 会话 | `runtime/session.py` 是线性追加 JSONL；`store.py` 从最后一份完整 transcript 恢复，产生新 task / sandbox | 节点 `id/parentId`、活动叶子、按祖先链重建；保留线性会话读取兼容 |
| 模型 | `runner.py` 每个 task 创建一个 `ModelGateway` session；Live 基于 `langchain-openai`，Replay 可确定性运行 | task 开始前选择已配置模型；保持 Gateway 协议 |
| 上下文 | `context.py` 有确定性 48 KiB 裁剪 | 只注入有界记忆摘要，不能裁掉 System 安全指令和完整工具调用组 |
| 评测 | `agentd/evals` 有 40 条合成 Prompt Injection 案例、Replay/Live 证据 | 增加独立的会话、记忆、检索、路由测评，原安全基线不改 |
| 检索 | 无 Milvus、ES、embedding/reranker、语料与同步协议 | 增加独立 retrieval 包、数据夹具和 Compose 开发服务 |
| 路由 | 无 Jev SDK、路由标签、成本表 | 增加 router 适配层；路由结果不得代表授权 |

当前项目已使用 `langchain-core` / `langchain-openai` 作为模型消息与客户端适配层；核心 Agent Loop 是手写的。树形 Session、分层记忆和检索编排都不需要引入 LangGraph。若未来出现多个复杂异步流程、持久 checkpoint 与人工中断，再以实际代码复杂度决定是否迁移。当前继续复用现有 LangChain 适配即可。

## 3. 当前环境审计

下表保留启动阶段的环境快照；开始实施后以 `PROGRESS` 的最新核对为准：

| 项目 | 结果 | 对实施的影响 |
|---|---|---|
| Docker | 客户端/服务端均 `29.2.1`，Compose `v5.1.0`；daemon 约 23 GiB / 12 CPU；`docker ps` 无运行中容器 | 可准备单独的 Milvus + ES 开发 Compose；尚未拉镜像或启动服务 |
| WSL | WSL 2；当前机器可见约 23 GiB RAM、约 202 GiB C 盘空间 | 先串行运行新服务，再决定是否同时运行 kind |
| GPU | RTX 5060，8 GiB 显存 | BGE 模型需先核对权重和显存；预留 CPU 路径 |
| Python | 系统 `python3` 是 3.10.12；`python3.12` 和 `uv` 当前不在 PATH | `agentd/pyproject.toml` 要求 `>=3.12,<3.13`，需用户态安装解释器与 uv，或使用合适的隔离容器 |
| 数据目录 | 仓库位于 `/mnt/c` | Session/记忆文件的 0700/0600 权限不能依赖未启用 metadata 的 DrvFS；运行时数据放 WSL 原生文件系统，Docker 数据用命名卷或原生 Linux 路径 |

后续已安装用户态 `uv 0.12.19`、Python 3.12.14，并按 `agentd/uv.lock` 同步依赖；Docker `hello-world` 已运行。用户指定 Milvus **2.5.10**，本项目独立 Compose 的 2.5.10/ES 已 healthy 且实际建索引与测评；固定版本、资源和结果见 [phase17](evidence/phase17-hybrid-retrieval-mvp.md)。

计划与用户态 Python 安装不需要 WSL 密码或 sudo。启动审计没有读取 API Key；后续已按用户明确授权执行 Live 首题，当前凭据与测评状态见 [phase21](evidence/phase21-jev-live-first-case.md)。

## 4. 四个最小闭环

### A. 会话树

- 在现有 JSONL 中追加不可变消息节点（`nodeId`、`parentId`、`sessionId`、`runId`、序号、脱敏公开消息）和活动叶子事件；旧线性文件可继续恢复。
- branch 选择一个**完整 Turn 结束**的节点作为父节点，新消息形成新叶子；原支线保持可查。恢复从选定节点沿 `parentId` 回溯，正向重建 transcript，生成新 task 与 sandbox。
- 工具调用和 ToolMessage 必须成组保存；不从未完成工具调用处恢复，也不重放历史工具副作用。恢复后应重新查询实时系统。
- 最小接口：列树、按节点读取路径、从节点分支、从指定叶子恢复。先把纯 Session 逻辑写成可独立运行的 CLI/测试，再接 REST。
- 测评：固定 JSONL 夹具验证父链、两条分支互不污染、重启后恢复一致、截断尾行容错、敏感字段脱敏、旧线性会话兼容。

### B. Codex 风格分层记忆

- 对已结束 Session 做第一阶段提取：生成有来源引用的简短 `raw_memories` 与 `rollout_summaries`，只记录稳定偏好、项目约束、反复有用的经验；原始外部日志和检索文档中的命令不能自动提升为可信指令。
- 第二阶段把多个提取结果整理成 `MEMORY.md` 与较短的 `memory_summary.md`；启动时只注入摘要，必要时按需读取详情与会话总结。保持每层字节预算与来源 ID。
- MVP 用本地文件、显式 `extract` / `consolidate` 命令和确定性 Replay，先避免后台调度、数据库租约、专用子 Agent 与自动写入技能。需要 Live 提取时再接现有 Gateway，单独记录模型费用和人工抽检结果。
- 记忆以项目为命名空间；演示先限单用户。提供 `forget` / 重新生成和版本可追溯性，避免过期事实无限延续。
- 测评：多会话事实回忆、过期事实更新、冲突处理、跨支线隔离、摘要预算、恶意日志不能改写安全规则；记录 Recall/Precision 与上下文 token 或字节占用。

### C. 混合检索

- 一份版本化语料、固定 `docId/chunkId` 和元数据，同时写入 Milvus dense 和 ES BM25；重建命令要幂等，失败时可从源语料全量重建，避免把双写误说成事务。
- 主链路：`query -> ES BM25 topN + Milvus dense topN -> RRF -> BGE Reranker topK -> 带来源的片段`。先使用小型公开英文语料和合成运维 runbook；中文语料需要另选分词器并单独比较。
- Milvus 内置 sparse/BM25 可作为 `dense + Milvus BM25` 的消融组，用于回答「为什么还要 ES」。ES 的倒排、过滤和 BM25 已承担主链路关键词检索，不能把两份 BM25 结果当作独立增益。
- Eval 数据记录 query、相关 `chunkId`、证据范围；比较 BM25、dense、RRF、RRF+rerank 的 Recall@K、MRR、nDCG@10、p50/p95 延迟和索引构建时间。公开集优先用 BEIR SciFact 作通用检索对照；项目运维集另行合成并公开标签。
- 先做检索离线测评，再让 Agent 调用受限 `search_knowledge`；RAG 文本必须以引用/来源身份进入模型，不能变成 System 指令，也不能直接触发外部写操作。

### D. Jev 路由

- 给任务定义少量可解释标签，例如 `simple_lookup`、`multi_step_diagnosis`、`security_sensitive`；Jev 只输出标签/置信度和建议模型，应用映射到已配置的模型 Gateway。
- 路由在 task 开始前发生，同一 task 内不自动换模型；保留 Replay 假路由器。超时、报错或低置信度时走配置的默认模型并记录原因。
- Eval 至少比较固定便宜模型、固定强模型、Jev 路由三组在**同一批**任务上的质量、延迟、总费用与错误路由率。仅测分类准确率不能证明路由省钱且保质。
- Live Jev 需要 TypeSafe API Key；真实模型横评还需要两个可用的下游模型、各自价格与一次明确的外部调用预算。以前 DeepSeek Live Eval 的授权已消费，不可复用。

## 5. 启动阶段的依赖清单（历史快照）

Python、Docker 镜像、本地模型和 SDK 均已备齐；以下保留最初依赖判断。当前唯一的服务凭据阻塞是 TypeSafe HTTP 401，不能据下表重新安装环境或重复申请已有授权。

| 项目 | 何时需要 | 当前结论 |
|---|---|---|
| Python 3.12、uv、锁定包 | 开始 Python 模块实施 | 本机缺少；可用户态安装，不需要提供 WSL 密码 |
| Docker 镜像与模型权重下载 | Milvus/ES/BGE 集成 | Docker 通了，但镜像、BGE 权重和 Jev SDK 尚未下载；记录版本、大小与来源 |
| LLM API | 真实记忆提取、Agent Live、路由横评 | Replay/结构测评不需要；Live 需要新授权与费用上限，不能读取仓库外秘密 |
| TypeSafe Jev Key | Jev Live 调用 | 未验证是否存在；假路由器不需要 |
| 两个下游模型 | 验证路由收益 | 最好有便宜/强模型各一个及可查的 token 价格；可先用 Replay 验证接线 |
| WSL 密码、sudo | 常规方案 | 不需要；若未来出现不可绕过的系统安装再单独说明 |

Milvus standalone、Elasticsearch 和 BGE 同时运行会占较多资源。先启动检索服务和小语料，确认内存、swap、显存，再考虑与原 kind 集群并存。不得清理或停止用户的其他容器。

## 6. 实施顺序、工作量和证据

以下为一人熟悉本仓库、依赖网络可用、仅做 MVP 的**估算工作日**，不是承诺工期：

| 步骤 | 估算 | 完成证据 |
|---|---:|---|
| M0：用户态 Python/uv、资源与源资料固定、离线夹具 | 1–2 天 | 环境清单、锁文件、可复现命令 |
| M1：Session 树与分支恢复 | 2–3 天 | 纯逻辑 Replay 与 API 例子 |
| M2：两阶段记忆与分层读取 | 3–4 天 | 多会话夹具、提取/整理/读取报告 |
| M3：Milvus + ES + BGE 检索链路 | 4–6 天 | Compose、数据索引、消融指标与来源证据 |
| M4：Jev 路由与回退 | 1–2 天 | Fake 路由回归；有新授权后 Live 小样本 |
| M5：整合、学习文档、Eval 报告 | 2–3 天 | 一键最小演示、指标/成本表、限制说明 |

合计约 **13–20 工作日**；网络下载、Windows/WSL 兼容、模型显存与 Live 授权等待会拉长时间。每个模块先独立可运行可测评，再接 Agent；不要把四个服务一次性塞进 Runtime。原 40 条安全 Eval 应保留为回归基线，但不把它们当成 RAG/记忆质量测评。

## 7. 恢复本工作时

1. 先读 `GOAL.md`、`AGENTS.md`、`docs/README.md`、`docs/24-项目全景与心智模型.md`、`docs/PROGRESS.md`，再读本文和 [32 学习手册](32-Agent记忆检索与路由学习手册.md)。
2. 检查 `git status`、Python/uv、`docker version`、`docker compose version`、容器与资源；不要假定 2026-09-28 的环境仍然有效。
3. M1 的实现与本地测评见 [33 树形 Session](33-树形Session与分支恢复学习手册.md)；M2 见 [34 分层记忆](34-Codex风格分层记忆学习手册.md)；M3 见 [35 混合检索](35-Milvus-ES-BGE混合检索学习手册.md)；M4 见 [36 Jev 路由](36-Jev模型路由学习手册.md) 与 [phase20 Live 无网络预检](evidence/phase20-jev-live-preflight.md)；M5 见 [phase19 联合 Replay](evidence/phase19-phase7-integrated-replay.md)。每做完一项更新 `PROGRESS`、模块学习文档和独立 evidence。
4. Replay、集成、Live 分栏记录；没有新授权不读取 Key、不向外部模型发送项目数据或 Eval 集。

## 8. 设计参考（官方原始资料）

- [Pi Session 文件格式](https://pi.dev/docs/latest/session-format)、[Pi Sessions 与树形导航](https://pi.dev/docs/latest/sessions)、[Pi SessionManager 源码](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts)。
- [Codex memories 架构说明](https://github.com/openai/codex/blob/main/codex-rs/memories/README.md) 及 [read/write 源码目录](https://github.com/openai/codex/tree/main/codex-rs/core/src/memories)。这是参考分层思想，不复制完整后台 job/状态数据库实现。
- [Milvus 全文 BM25](https://milvus.io/docs/full-text-search.md)、[Milvus Docker Compose](https://milvus.io/docs/install_standalone-docker-compose.md)、[Elasticsearch Docker](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/install-elasticsearch-docker-basic)、[Elasticsearch 混合检索](https://www.elastic.co/docs/solutions/search/hybrid-search)。
- [TypeSafe Jev 意图路由](https://docs.typesafe.ai/patterns/intent-routing)、[Python SDK](https://github.com/typesafe-ai/typesafe-sdk-python)。
- [BEIR 数据集](https://github.com/beir-cellar/beir)、[Ragas 指标](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/)；Ragas 是测评库，不是一个固定公开数据集。

## 9. Phase 7 历史验收对照（2026-09-29）

核对了当时的代码、测试断言、phase15–21 的实际记录和 GitHub PR。以下是 Phase 7 的历史状态，不是现行 Phase 8 待办；当前目标见 [37](37-Kubernetes运维Agent目标与验收.md)。Jev Live 横评当时未完成，用户随后明确将 Jev 移出新目标。

| 要求 | 当前实现与权威证据 | 验收状态 |
|---|---|---|
| Session 节点、父链、活动叶子、完整 Turn 分支、旧格式与断尾 | `runtime/session.py`、`test_session.py`；[phase15](evidence/phase15-session-tree-mvp.md) | 本地确定性验证完成 |
| 恢复新 task/sandbox，不重放历史副作用 | `store.py::resume` 创建新 task；`runner.py::run` 每次 claim；历史 ToolMessage 只作为上下文；身份与分支测试 | 代码路径与 Replay 验证完成；本阶段未重跑真实 gVisor 分支 E2E |
| 记忆提取、整理、渐进读取、来源/时间/冲突、重建/遗忘 | `memory.py`、`memory_cli.py`、`test_memory.py`；[phase16](evidence/phase16-memory-mvp.md) | 显式格式 MVP 完成；当前事实 P/R 均 2/2，冲突 1，摘要 155/256 字符 |
| 记忆产物与低信任边界 | raw memories、会话摘要、`MEMORY.md`、`memory_summary.md`；来源校验与恶意工具日志测试 | 本地完成；自然语言 LLM 提取质量未测，不属于此次 Jev 调用授权 |
| 同一语料/ID 的 ES BM25 + Milvus dense、RRF、BGE 重排 | `retrieval/`、Compose 与 [phase17](evidence/phase17-hybrid-retrieval-mvp.md) | 真实 Docker/CPU 集成完成；Milvus 2.5.10 |
| 公开集与合成 runbook，质量/延迟/资源消融 | SciFact 5,183 文档、固定 30 query；runbook 9 chunks/7 query；phase17 完整表 | 完成固定子集测评；Milvus 原生 BM25 为可选项，未实现 |
| Jev 每 task 一次选模、回退、成本计入、不改变权限 | `router.py`、`runner.py`、真实 SDK MockTransport 与 Fake/Replay；[phase18](evidence/phase18-jev-router-mvp.md) | 本地验证完成 |
| 固定便宜/强模型/Jev 的真实同题质量、费用与延迟 | [phase21](evidence/phase21-jev-live-first-case.md) 仅首题；Jev 失败回退、IPv4 鉴权 401 | Phase 7 未完成；Phase 8 不继续 |
| 四模块接入、手写 Loop、LangChain/LangGraph 取舍 | [phase19](evidence/phase19-phase7-integrated-replay.md)、本文第 2 节和 [32](32-Agent记忆检索与路由学习手册.md) | 联合 Replay 完成；真实检索、假 Sandbox/Jev、Replay LLM |
| 中文学习文档、命令、限制、版本、脱敏证据与 GitHub | docs33–36、phase15–21、`PROGRESS`；[PR #1](https://github.com/chx739/sandboxd/pull/1) | 已交付，未合并 main；Live 结果仍待补齐 |

### 最小演示入口

在仓库根目录运行。下列命令不消耗外部模型 API；检索需先按 [35 的服务与索引命令](35-Milvus-ES-BGE混合检索学习手册.md) 准备 Docker 和固定版本 BGE 模型。Session 和记忆的可写数据使用 WSL 原生目录，示例 Session 仅用于读取。

```bash
# A：显示公开夹具的树；分支和恢复接口见 docs/33。
uv run --project agentd --frozen --extra rag --extra jev python -m agentd.session_cli \
  --session-dir agentd/testdata/session-tree-demo tree session-0123456789abcdef

# B：合成跨会话提取、更新、冲突和恶意日志测评。
uv run --project agentd --frozen --extra rag --extra jev python -m agentd.memory_eval

# C：已有 runbook 索引上的真实混合查询与四组消融。
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --project agentd --frozen --extra rag --extra jev python -m agentd.retrieval.cli \
  query 'How do I investigate a CrashLoopBackOff pod?' --top-k 3
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --project agentd --frozen --extra rag --extra jev python -m agentd.retrieval.cli eval

# D：Phase 7 历史 Fake/Replay 路由测评；Phase 8 不运行此组命令。
uv run --project agentd --frozen --extra rag --extra jev python -m agentd.router_eval
uv run --project agentd --frozen --extra rag --extra jev python -m agentd.router_live_eval
```

SciFact 的 nDCG@10：BM25 **0.5511**、dense **0.7445**、RRF **0.7931**、rerank **0.7826**；完整 Recall/MRR、延迟、资源口径以 phase17 为准。Jev Fake 数字是教学夹具，首题 Live 回退也不能替代完整横评。
