# Ops-v1 固定知识库与候选评测集

## 来源、授权与加工

- 32 篇 Prometheus Operator Runbooks，`a685d14cf5128bb30e2bf935c3983decd772d885`，Apache-2.0。
- 6 篇 Kubernetes website 英文文档，`4d668b948124af2f4d3d6c2c01ea3c67dd0f0bf1`，CC-BY-4.0，版权归 Kubernetes 文档贡献者。
- 2 篇本项目编写的**合成**故障说明，不是真实生产事故。
- 每篇的永久链接、原始 SHA256、许可证和标题在 `manifest.json`。原文在 `sources/`，完整上游许可证在 `licenses/`。
- `corpus.jsonl` 是加工版本：去除 YAML front matter、Hugo shortcode，按 Markdown 标题和段落切块；298 chunks。没有翻译官方正文，没有把 QA 加入正文。
- 来源 commit 是内容版本，不代表所有内容都适用于任意 Kubernetes 集群版本。新功能、feature gate、存储实现等必须再核对实际环境。
- 上游文档保留了 TODO 等缺口；不能用模型生成的知识填补后冒充来源原文。

## 评测契约

`queries.jsonl` 60 题：10 精确、15 症状、15 步骤、10 多文档、10 无答案。59 中文、1 英文。13 dev / 47 test，按问题族 SHA256 分配；同族改写不跨集合。

题目、要点和 qrels **均为模型编写、待人工审核**。自动检查只验证引用存在、快照匹配与分组一致，不证明标签完整或语义正确。逐题待办见 `REVIEW.md`。题目中指定的相关 chunks 可能漏标其他同样有效证据，正式使用前需人工补齐。

无答案题的 `relevance={}`，不纳入检索 Recall/MRR/nDCG 分母；`evidenceRefs` 可包含证明来源缺口的片段。无答案表现应在生成/拒答测评单列，不能靠检索返回空列表的比例替代。

这是一套自建、来源固定的候选集，不是公开社区榜单或人工金标。测试集已经用于首轮报告；之后调整配置只能看 dev，需要新盲测集才能声称新的泛化收益。

## 离线重建与验证

```bash
agentd/.venv/bin/python -m agentd.retrieval.ops_questions
agentd/.venv/bin/python -m agentd.retrieval.cli check
agentd/.venv/bin/python -m unittest agentd.tests.test_ops_dataset -v
```

已经提交原文、manifest 和 corpus，因此安装/测评无需每次访问上游。原始抓取缓存不进 Git；重新获取时只能使用 manifest 中的固定 URL，并核对 SHA256。`ops_snapshot.py` 是原始构建器，输入需为相同 commit 的上游缓存。
