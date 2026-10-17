# Phase 17：混合检索本地集成证据

时间：2026-09-28。类型：**Docker 本地集成 + CPU 本地模型**；没有外部 LLM、Jev、gVisor 或 Agent Live 调用。

## 固定对象

- Milvus `milvusdb/milvus:v2.5.10`、PyMilvus `2.5.6`；ES `8.19.22`；etcd `3.5.18`；MinIO `RELEASE.2023-03-20T20-16-18Z`。Compose 项目为 `sandboxd-rag`，4 个容器均 healthy；ES/Milvus 端口仅绑定 `127.0.0.1`。
- BGE-small-en-v1.5 revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`（384 维）；BGE-reranker-base revision `2cfc18c9415c912f9d8155881c133215df768a70`。本地 `safetensors`，CPU 推理、HF/Transformers 离线模式。
- 合成 runbook：9 chunks、7 query；语料 SHA256 `be1ddb6e642b55a1412c392412e18ae5891fc8553edb7551d144fe78b8979190`。
- BEIR SciFact 官方 ZIP MD5 `5f7d1de60b170fc8027bb7898e2efca1` 校验通过；完整 5,183 篇 corpus，test 300 条 qrel query；语料 SHA256 `9a1ac93599741b171788a9d9347ad98a5da088ee2a7280b234c6ad572bba2815`。评测只取按数字 ID 排序的前 30 条 test query。

## 构建与质量

`rebuild` 对合成 runbook：ES 9、Milvus 9，384 维 FLAT/COSINE；embedding 126.5 ms、ES 376.1 ms、Milvus 3536.48 ms。

`rebuild` 对 SciFact：ES 5,183、Milvus 5,183，384 维 FLAT/COSINE；CPU embedding **352,482.96 ms**、ES 1,503.75 ms、Milvus 4,268.19 ms。此时间依赖当前机器和模型缓存，非性能承诺。

| SciFact 前 30 test query | Recall@1 | Recall@5 | Recall@10 | MRR | nDCG@10 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| ES BM25 | 0.3500 | 0.6233 | 0.7300 | 0.5159 | 0.5511 | 9.23 | 17.97 |
| Milvus dense | 0.6067 | 0.8100 | 0.8600 | 0.7157 | 0.7445 | 14.79 | 25.31 |
| RRF top20 | 0.7067 | 0.7967 | 0.8433 | 0.8030 | 0.7931 | 24.52 | 36.90 |
| RRF + BGE rerank | 0.6333 | 0.8467 | 0.8767 | 0.7492 | 0.7826 | 5759.99 | 6152.91 |

Rerank 在 Recall@10 高于 RRF，但 nDCG@10 和 Recall@1 下降；重排并非此设置下的全面改进。30 条是固定小子集，不能声称 BEIR 完整基准分数。合成 runbook 7 条四组 Recall@1/5/10、MRR、nDCG@10 全 1.0，是接线 smoke，不用于宣称增益。

公开集运行进程 `processMaxRssMB=1886.98`；索引后 `docker stats --no-stream`：Milvus 172.2 MiB、ES 1.046 GiB、etcd 28.87 MiB、MinIO 109 MiB。各值是观测瞬间，Docker cgroup RSS 与 Python max RSS 口径不同，不能相加当作精确峰值。运行前 WSL `available≈20 GiB`、swap 0。

## 接线与边界

`agentd.retrieval.cli check/rebuild/eval` 已在真实 ES/Milvus 与本地模型执行。`search_knowledge` 仅在显式配置语料路径时注册；Policy 只允许有界 `query` 与 `topK 1..3`，返回来源和 `untrusted-retrieved-evidence`。合成恶意片段测试证明原文仍被当作低信任数据，外部索引返回未知 ID 时拒绝。它没有触发 Agent Live、Kubernetes 写入或审批。

对已建索引的 runbook，真实调用 `KnowledgePlugin.execute("search_knowledge", {"query": "How do I investigate a CrashLoopBackOff pod?", "topK": 2})` 返回 HTTP 200，首项 `runbook:crashloop`，来源 `synthetic://sandboxd/runbook-v1/crashloop`，顶层信任标记为 `untrusted-retrieved-evidence`。此验证经过 Agent 工具适配层、Policy、ES/Milvus 和本地 BGE，但没有启动完整 Agent Task 或调用外部 LLM。

Python `unittest discover -s agentd/tests -v` **53/53** 通过，`git diff --check` 通过。完整测试因 WSL `/mnt/c` 上的 CLI 子进程启动较慢，耗时 256 秒；不要把这当作模型推理或检索延迟。

本页仅记录实际命令输出和明确边界；如何复现、原理及面试讲法见 [35 学习手册](../35-Milvus-ES-BGE混合检索学习手册.md)。
