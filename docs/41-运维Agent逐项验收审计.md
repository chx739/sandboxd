# Phase 8 逐项验收审计

2026-09-29；依据 [完整目标](37-Kubernetes运维Agent目标与验收.md)，按实际代码和已保存原始结果检查。**目标尚未完成**。本表不是新的缩减版目标，也不以已有测试数量代替全部验收。

## 1. 功能与确定性证据

| 要求 | 当前证据 | 结论与边界 |
|---|---|---|
| Pi 会话节点、父链、分支与重启恢复 | `agentd/app/runtime/session.py`；`test_session.py` 的 branch/restart、selected-node resume 测试；[72项记录](evidence/phase24-python-tests.txt) | 已有可运行实现；从完整 Turn 分支，恢复创建新 task，不重放工具副作用 |
| 分支隔离、工具组、断尾、旧记录兼容 | `test_session.py` 的 incomplete-tool、tool-group、truncated-tail、old-linear 测试 | 已验证当前路径会话隔离；项目长期记忆是共享的，不能宣传为每分支独立记忆 |
| Codex 分层记忆、来源、冲突、重建、遗忘、预算 | `test_memory.py`；[记忆评测](evidence/phase24-memory-eval.json)；[源码对照](39-Pi与Codex源码对照.md) | 规则/显式 MEMORY 标记的最小实现已验证；不是自然语言抽取质量评测 |
| Milvus 2.5.10 原生 BM25/Dense/RRF/BGE | `retrieval/milvus_hybrid.py`、`pipeline.py`；[60题结果](evidence/phase23-ops-rag-results.json) | 真实本地服务与模型执行；新默认路径不需要 ES，历史 ES 保留 |
| 导入、更新、删除、重建、过滤 | `retrieval/snapshot.py`、`acceptance.py`；[生命周期证据](evidence/phase24-index-lifecycle.json) | 不可变快照，新索引显式切换；旧索引保留；非在线无停机迁移 |
| 来源和版本过滤 | corpus 元数据与 `MilvusHybrid` 字段白名单 | source_revision 是内容版本，不自动判定 Kubernetes 运行版本适配性 |
| 30–50篇固定文档、许可和模型版本 | ops-v1 的40篇manifest、sources、licenses、embedding/reranker-lock | 已交付；38篇公开上游、2篇合成项目记录，不能称真实生产事故 |
| OpenSearch 日志字段与聚合 | `logs/opensearch.py`、`app/plugins/logs.py`；[40题结果](evidence/phase25-logs-bounded-results.json) | 实际查询720条合成日志；参数受限，模型工具结果超过3500字节明确拒绝 |
| 3类联合排障 | [联合回放](evidence/phase23-joint-replay.json) | 本地服务真实、决策脚本/Fake Sandbox；回放3/3，真实 LLM尚未验证 |
| Jev 最小路由 | `app/router.py`、`test_router.py`、`router_live_eval.py`；[两题计划](evidence/phase23-jev-plan.json) | SDK/回退/每task一次已有测试；新Key只读鉴权成功，付费选模未验收 |

## 2. 测评要求

| 要求 | 当前证据 | 未完成或不能推导的结论 |
|---|---|---|
| 60题、类别、来源、参考要点、改写不跨集合 | ops-v1 `queries.jsonl`、`REVIEW.md`；`test_ops_dataset.py` | 全部为模型候选；审核清单未勾选，不能称人工金标 |
| 四组 Recall@5/10、MRR、nDCG与分集合结果 | [逐题检索报告](evidence/phase23-ops-rag-results.json) | 已实际执行；测试集已看过，后续调参不能冒称盲测 |
| 检索/重排耗时、资源、费用、失败分析 | 同报告、[学习手册§6](38-运维Agent与测评学习手册.md) | 有阶段耗时与RSS/API费用；不是包含生成的端到端延迟 |
| 无检索 vs RAG、Ragas与运维 rubric | `ops_eval/generation.py`、`ragas_adapter.py`；[3项协议测试](evidence/phase24-ragas-protocol-tests.txt) | 只有真实Ragas类+Fake响应协议测试；无真实生成分数和模型裁判依据 |
| 全60题生成对比 | [完整离线计划](evidence/phase26-generation-full-plan.json) | 最多960请求、保守预留$46.714061，未授权/未执行；此前6题仅冒烟，不能替代此项 |
| 日志独立预期、时间边界、集合P/R与聚合 | `logs/oracle.py`、`test_ops_logs.py`；[40题报告](evidence/phase25-logs-bounded-results.json) | 已验证固定参数的实际结果；Schema接受率不是模型参数生成正确率 |
| Benchmark固定负载、吞吐与延迟 | [首轮CSV](evidence/phase23-opensearch-benchmark.csv)、[环境](evidence/phase23-opensearch-benchmark-environment.json) | 720条、1客户端、20ops/s限速、20预热/100测量，不是生产吞吐能力 |
| 性能运行时资源占用与P95 | `logs/benchmark/run.py`；[补测](evidence/phase26-benchmark-p95-resources.json)和[原始指标](evidence/phase26-benchmark-p95-resources-raw.json) | 已执行P50/P95/P99与48次节点CPU/JVM采样；不冒充容器RSS或宿主机CPU，监控自身有开销 |
| 联合 Agent真实模型工具决策、参数、引用 | `ops_eval/live_agent.py`；[3场景计划](evidence/phase24-live-agent-plan.json) | 尚未执行，最多18请求；语义正确性仍需审核，不以ID存在代替证据支持 |
| 裁判依据与人工抽查 | 实现已逐请求保存可见答案、判分依据和预算账本 | 真实评分不存在；不能代签人工审核 |

## 3. 交付和下一步

- 安装、启动、导入、演示、检索/日志测评命令：[38](38-运维Agent与测评学习手册.md)。隔离Ragas/Benchmark环境和费用门：[40](40-运维评测运行与费用门.md)。Pi/Codex commit 与简化：[39](39-Pi与Codex源码对照.md)。
- 实现分支 `codex/agent-memory-rag-plan`，[PR #1](https://github.com/chx739/sandboxd/pull/1) 已推送，保持未合并；历史数据和报告保留。
- 前一轮完整72项后有新的定向测试；不把72项记录说成最终每一行代码都已重测。
- 下一步按用户明确批准的模型/样本/次数/预算执行真实验证。现有两项预算问题（Jev $0.10、生成6题+联合3场景 $6）未获答复，自动目标续跑不是费用授权。
- 完整60题生成计划独立于6题冒烟。先验证协议与输出，再决定是否批准全量；本次只保存计划，没有追加费用或暗中扩充已询问的范围。
- 待人工审核列表继续保留；模型可解释和提出修订候选，但不能把自己的复核改记成人工审核。
