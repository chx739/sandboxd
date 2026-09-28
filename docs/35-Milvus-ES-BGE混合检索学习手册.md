# Milvus 2.5.10 + ES BM25 + BGE：混合检索 MVP

## 1. 一分钟讲法

同一份带版本和 SHA256 的语料分别进入 Elasticsearch 倒排索引和 Milvus dense 向量索引。查询时，ES BM25 找精确术语，BGE-small 向量找语义相近内容；RRF 只融合排名，不混用不可比较的原始分数；BGE-reranker-base 对候选逐对重排。结果带 `chunkId`、`source` 和 `trustLevel`，Agent 的静态 `search_knowledge` 工具只能读，检索文本不能改变工具权限。

```text
versioned JSONL -> ES text/BM25 ---------+
                -> BGE-small -> Milvus FLAT/COSINE --+-> RRF top20 -> BGE reranker -> evidence
query ----------------> ES BM25 ---------+                ^
      -> BGE-small query embedding -> Milvus dense ----+
```

## 2. 读代码顺序与关键取舍

1. `agentd/retrieval/core.py`：`Chunk`/`QueryCase` 契约、语料 hash、RRF、Recall/MRR/nDCG 与延迟百分位。
2. `elasticsearch_bm25.py`：原生 `text` 倒排索引，默认英文 analyzer，BM25；`chunkId`/`corpusHash` 同步保存。
3. `milvus_dense.py`：用户指定的 Milvus **2.5.10**；小语料用 `FLAT/COSINE` 精确索引，384 维向量来自本地 BGE-small。Python SDK 固定 2.5.6。
4. `bge_local.py`：固定本地模型目录，CPU 推理；英文 query 使用官方 instruction，passage 不加前缀。
5. `pipeline.py`：两路召回、RRF、交叉编码器重排；只返回本地语料中存在的 ID，证据降权。
6. `cli.py`：`check/rebuild/query/eval`；`scifact.py`：官方 SciFact ZIP 校验与确定性转换。
7. `app/plugins/knowledge.py` 与 `app/policy.py`：Agent 静态只读工具、参数上限和低信任标签。

ES 已负责主链路的 BM25 倒排。Milvus 2.5 也有内置 BM25，但再加一份 BM25 并不自然形成独立增益；它可以用于以后比较是否省掉 ES。此 Demo 要展示 ES 的倒排能力，故 Milvus 只存 dense。两份索引不是事务：失败时从同一份 JSONL `rebuild`，索引名称带版本与 hash，查询前检查两边数量。版本、hash 和计数检查不等于逐行一致性的生产级审计。

## 3. 复现命令

在仓库根目录执行。Docker Compose 只发布 localhost 端口；ES 关闭认证仅供本机 Demo，不能直接部署到公网。BGE 权重放在 WSL 原生用户目录，不提交 Git。安装可选依赖时 `uv` 从锁文件同步；如本机镜像不可用，按 `pyproject.toml` 的固定版本从可信包源获取。

```bash
docker compose -p sandboxd-rag -f deploy/retrieval/compose.yml --profile es up -d
uv sync --project agentd --frozen --extra rag
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --project agentd --frozen --extra rag python -m agentd.retrieval.cli check
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --project agentd --frozen --extra rag python -m agentd.retrieval.cli rebuild
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --project agentd --frozen --extra rag python -m agentd.retrieval.cli eval
```

模型：`BAAI/bge-small-en-v1.5` revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` 和 `BAAI/bge-reranker-base` revision `2cfc18c9415c912f9d8155881c133215df768a70`，各自下载到 CLI 默认目录，保存 `safetensors` 权重和配置。下载后设离线变量，所有 embedding/rerank 均在 CPU 本地运行，无外部推理 API。

公开集：[BEIR SciFact](https://github.com/beir-cellar/beir/wiki/Datasets-available)，官方 ZIP URL 和 MD5 固定在 `scifact.py`。转换器不解压到仓库；完整 5,183 篇语料、test 300 条有标签 query 写入 `/tmp`。`--max-queries 30` 取按数字 query ID 排序的前 30 条，属于**固定子集**，不能与 BEIR 官方完整榜单直接比较。

```bash
curl -fL -o /tmp/sandboxd-scifact.zip \
  https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip
uv run --project agentd --frozen --extra rag python -m agentd.retrieval.scifact \
  /tmp/sandboxd-scifact.zip /tmp/sandboxd-scifact-data
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --project agentd --frozen --extra rag python -m agentd.retrieval.cli \
  --corpus /tmp/sandboxd-scifact-data/scifact.corpus.jsonl \
  --queries /tmp/sandboxd-scifact-data/scifact.queries.jsonl rebuild
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --project agentd --frozen --extra rag python -m agentd.retrieval.cli \
  --corpus /tmp/sandboxd-scifact-data/scifact.corpus.jsonl \
  --queries /tmp/sandboxd-scifact-data/scifact.queries.jsonl eval --max-queries 30
```

Agent 工具默认不注册；显式设置 `AGENTD_RETRIEVAL_CORPUS` 和 `AGENTD_RETRIEVAL_QUERIES` 后注册。模型目录可通过 `AGENTD_RETRIEVAL_EMBEDDING_DIR`、`AGENTD_RETRIEVAL_RERANKER_DIR` 覆盖。工具首次调用才加载模型，需先 `rebuild`；用于运维演示时建议选择合成 runbook 语料，SciFact 是检索评测集，不是运维知识库。

## 4. 已测结果与指标口径

完整数值见 [phase17 evidence](evidence/phase17-hybrid-retrieval-mvp.md)。SciFact 固定 30 query 子集上，BM25、dense、RRF、rerank 的 nDCG@10 分别为 **0.5511、0.7445、0.7931、0.7826**；reranker 的 Recall@10 最高 **0.8767**，但 nDCG 低于 RRF，CPU p50 达 **5.76 秒**。因此不声称重排全面提升。合成 runbook 7 条 query 四组全为 1.0，只证明接线和标签契约，不能证明质量收益。

Recall@K 用每条 query 在前 K 找到的相关文档数除以该 query 的相关文档数，再对 query 平均；MRR 看第一个相关结果位置；nDCG@10 用 graded qrel 和理想排序归一化。延迟是同一进程依次查询得到的 p50/p95，不含索引构建和进程冷启动。BM25、dense 是各自单路耗时；RRF 与 rerank 是端到端耗时。`processMaxRssMB` 只含 Python 进程，Docker 内存单独记录。

## 5. 面试追问

- **为什么 ES 和 Milvus 各存一份？** 一个侧重词项匹配，一个侧重语义向量；共享 ID/hash 才能融合。ES 的 BM25 在本项目是倒排检索主链路，Milvus 内置 BM25 可以替代 ES 做后续对照。
- **为什么用 RRF？** ES 的 BM25 分数和向量 cosine 不能直接相加；RRF 只使用名次，简单稳定。权重和常数仍可在验证集上调，当前不为小样本过拟合。
- **为什么 reranker 反而更慢且 nDCG 下降？** cross-encoder 对 20 篇长文逐对推理，CPU 开销大；相关性模型和 SciFact 标签未必完全对齐。应看质量与延迟的同一批数据，而不是假设加层必然更好。
- **RAG 文档含恶意指令怎么办？** `trustLevel` 明示低信任，模型最多把它作为证据；Python Policy、Go Tool Policy 与审批门仍决定能否执行工具。Prompt 标注本身不是安全边界。
- **生产化还缺什么？** 原子切换双索引、权限与认证、文本分块策略、增量同步、监控、中文 analyzer/embedding、充分样本与统计置信区间。此 Demo 仅为本机单用户。

## 6. 原始资料

- [Milvus 2.5.10 Release](https://github.com/milvus-io/milvus/releases/tag/v2.5.10)、[Milvus 2.5 standalone Compose](https://milvus.io/docs/v2.5.x/install_standalone-docker-compose.md)、[Milvus 2.5 全文检索](https://milvus.io/docs/v2.5.x/full-text-search.md)。
- [Elasticsearch Docker](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/install-elasticsearch-with-docker)、[BGE-small 模型卡](https://huggingface.co/BAAI/bge-small-en-v1.5)、[BGE-reranker 模型卡](https://huggingface.co/BAAI/bge-reranker-base)。
