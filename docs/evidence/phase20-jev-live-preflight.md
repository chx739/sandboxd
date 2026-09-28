# Phase 20：Jev 真实横评的无网络预检

时间：2026-09-28。本页保留授权前的无网络预检：所有质量测试结果均为本地假模型。后续用户已批准该范围，真实首题、TypeSafe HTTP 401 和恢复边界见 [Phase 21](phase21-jev-live-first-case.md)。

## 固定协议

- 数据：`agentd/testdata/router-live-v1.jsonl` 的 8 条公开合成运维题。每条有简短证据、三个候选代码、人工标注的答案和期望路由等级；没有真实集群日志、密钥或用户内容。预览对解析后的有序案例做规范 JSON SHA256：`5c7c0cb67643663c3ee794af69625f2a6651654cbbff3ebfd4f2d23730848a75`；执行前若它变化，应重新审核授权范围。
- 同题三组：固定 `deepseek-flash`、固定 `deepseek-v4-pro`、TypeSafe Jev `Choice` 后选择两者之一。每题最多 1 次 Jev + 3 次下游调用，全轮最多 **8 次 Jev + 24 次 DeepSeek**。第一题出现 Provider 错误或 Jev 超时/报错便停止。
- 模型输出上限 128 token，API 客户端不重试，串行运行。Jev 仍使用应用的 0.7 置信度门与默认强等级回退；路由不改变 Agent 工具权限。
- 答案质量只按合法 JSON 中的精确 `code` 比对；同时报告路由等级正确率、三组准确率、错误数、p50/p95 延迟和包含 Jev 的费用估算。它测的是**合成证据判断**，不是完整 gVisor Agent E2E 或生产任务质量。
- 费用门对每个请求预留 4096 输入 token，下游再加 128 输出 token；这是保守估算，**不是服务端输入 token 硬上限或账单保证**。响应提供用量时按用量计价；缺失用量或报错时保留预留额。发生实际超额会在下一次请求前停止。`--execute` 还要求传入预览显示的案例 SHA256，不匹配就不会创建 Provider Client。

2026-09-28 核对的公开**峰时、缓存未命中**单价：DeepSeek Flash 输入/输出每百万 token 为 `$0.30/$1.20`，V4 Pro 为 `$1.32/$3.96`，见 [官方模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)；Jev 输入每百万 token `$0.042`，见 [TypeSafe 官方说明](https://typesafe.ai/blog/introducing-system-one-models-and-jev)。据此 8 题完整计划的事前预留为 **$0.107053**。模型供给和价格会变，执行前须重新核对并显式配置；`strong` 是本实验的部署等级名，不预设 V4 Pro 一定比 Flash 准。

## 已运行的无网络命令

```bash
uv run --project agentd --frozen --extra jev python -m agentd.router_live_eval
uv run --project agentd --frozen --extra jev python -m unittest agentd.tests.test_router_live_eval -v
```

第一个命令默认只打印 `kind=reviewable-live-eval-plan-no-network`、8 个案例 ID、8/24 请求上限和 `$0.107053` 预留；不创建 Provider 客户端。第二个命令使用 Fake Source/Gateway 验证三组确实使用同一批题、无 Key 前预算门拒绝以及首题服务错误即停止。假模型 4/8、8/8、8/8 的答案正确数只验证评分路径，不是 DeepSeek 或 Jev 成绩。

验证结果：新增入口后完整 Python `unittest discover -s agentd/tests -v` **61/61**，275.780 秒。完整耗时主要来自 WSL `/mnt/c` 上的记忆 CLI 子进程；不代表 Jev 或模型延迟。随后加入执行前案例 SHA256 门禁，并定向验证 `test_router_live_eval` **5/5**、默认预览、`compileall` 与 `git diff --check`，均通过。完整 61/61 发生在 SHA256 门禁这次小改动之前，不能替代这次定向验证。

## 待授权执行

拟请用户明确授权：**TypeSafe Jev 最多 8 次 + DeepSeek Flash/Pro 合计最多 24 次，限上述 8 条合成样本，总费用上限 $1 美元**。执行前重新核对价格；若模型/价格不可用，先停下，不替换服务或扩样本。Key 只从进程环境注入；不自动读取 `secrets/`，不写入仓库或报告。获得授权后才可运行：

```bash
# 先在进程环境安全配置 AGENTD_LLM_API_KEY、TYPESAFE_API_KEY、
# AGENTD_ROUTER_PRICES_JSON、AGENTD_JEV_INPUT_USD_PER_MILLION。
uv run --project agentd --frozen --extra jev \
  python -m agentd.router_live_eval --execute --max-usd 1.0 \
  --expected-case-sha256 5c7c0cb67643663c3ee794af69625f2a6651654cbbff3ebfd4f2d23730848a75
```

若用量缺失、价格变化或首题服务报错，报告须明确标注不完整；不能以本地 Fake 结果补充或冒充 Live 样本。三组顺序固定可能受缓存影响，小样本不能推出普遍省钱或质量结论。
