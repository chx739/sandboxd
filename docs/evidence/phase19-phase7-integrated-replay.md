# Phase 19：Phase 7 四模块联合 Replay

时间：2026-09-28。环境：本项目 `sandboxd-rag` Docker Compose 的 Milvus 2.5.10、ES 8.19.22、etcd、MinIO；本地 CPU BGE 权重。Session、Memory 写入 `/tmp` 原生 Linux 临时目录；Fake Sandbox、Fake Jev、Replay LLM。**没有 TypeSafe、下游 LLM、真实 Kubernetes/gVisor 调用。**

运行：

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --project agentd --frozen --extra rag --extra jev \
  python -m agentd.phase7_demo
```

关键输出：

```text
kind=local-docker-rag-plus-deterministic-agent-replay
status=succeeded
route.effectiveTier=economy, route.source=deterministic-fake
model=phase7-replay-economy
searchToolCompleted=true
retrievalTrustMarked=true
memorySummaryInjectedAsData=true
sessionNodeCount=6
branchableLeaf=true
sandboxReleased=true
externalModelCalls=0
```

流程：先从成功结束的合成 Session 显式提取 `response_language=zh`，整理 `memory_summary.md`；新 Task 的 Runner 在领取假沙箱前经 Fake 路由选择经济 Replay Gateway；Replay 首轮调用静态 `search_knowledge`，工具从真实 ES/Milvus 和本地 BGE 返回带来源片段；第二轮给出明确标记为 Replay 的结论；Session JSONL 保存完成 Turn，活动叶子可 `load_branch`，假沙箱释放一次。

它验证四个模块的接线与低信任标签；不能替代真实 Agent Live、真实沙箱或 Jev 效果测评。独立质量和资源结果分别在 phase15–18，特别是 SciFact 30 条固定子集与 Jev 8 条教学夹具不得混为同一类证据。
