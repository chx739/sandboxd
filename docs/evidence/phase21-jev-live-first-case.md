# Phase 21：真实模型首题与 Jev 鉴权阻塞

日期：2026-09-28。状态：**DeepSeek 首题三次调用完成；Jev 回退，完整 8 题横评未完成**。

## 授权与执行范围

用户已明确批准 Phase 20 的固定范围：8 条合成题，最多 8 次 TypeSafe Jev 推理、24 次 DeepSeek 推理，总费用上限 $1；128 输出 token、串行、零重试、首题 Provider 错误即停。完整案例 SHA256 为 `5c7c0cb67643663c3ee794af69625f2a6651654cbbff3ebfd4f2d23730848a75`。

最初在受限执行沙箱内启动的进程在 1100 秒后终止，没有输出结果；该环境对 `api.deepseek.com` 的 DNS 解析失败。该尝试没有可核实的逐请求记录，不能从超时本身断言账单为零。此后增加仅含案例 ID、服务和模式的请求前进度标记，并在允许联网的环境执行。

联网执行的报告与进度标记一致：**1 次 Jev 尝试、3 次 DeepSeek 尝试，完成 l01 后停止**，`stoppedReason=first_case_provider_error`。未继续 l02–l08，也未重跑 l01。报告见 [脱敏 JSON](phase21-jev-live-first-case.json)。没有发送真实集群日志、用户文档或密钥正文；Key 由用户指定的仓库外文件读取后仅注入子进程环境。

## 实际结果

| 组 | 首题正确数 | 下游错误 | 延迟 | 估算费用（美元） |
|---|---:|---:|---:|---:|
| 固定 DeepSeek Flash | 1/1 | 0 | 689 ms | 0.00003150 |
| 固定 DeepSeek V4 Pro | 1/1 | 0 | 978 ms | 0.00013068 |
| Jev 路由后回退 V4 Pro | 1/1 | 0 | 1530 ms | 0.00030271 |

三组总估算 `$0.00046489`。路由组包含 Jev 失败请求的保守预留 `$0.00017203`，并非实际 Jev 账单。价格沿用执行前核对的 [DeepSeek 官方峰时价格](https://api-docs.deepseek.com/quick_start/pricing/) 与 [TypeSafe 官方价格](https://typesafe.ai/blog/introducing-system-one-models-and-jev)，账单以服务商为准。

Jev 的 `requestedTier/confidence/routerInputTokens` 均为空，`fallbackReason=router_error`，599 ms 后走默认 `strong`。**这证明了真实下游调用和错误回退，不能证明 Jev 的分类质量、省钱效果或完整 8 题横评通过。** 单题 p50/p95 等于该题延迟，不具有分布意义。

## 根因诊断与本地修正

- TypeSafe 只读模型列表请求默认连接超时；不带 Key 的默认连接检查也超时。
- 强制 IPv4 后，未认证 HEAD 请求在约半秒内收到 405（该入口要求 GET），说明网络路径可以到达服务。
- 使用现有 Key、IPv4 和官方 SDK 读取模型列表，返回 `TypeSafeAuthenticationError`、HTTP **401**。不能将其解释为有效 Key 或已取得 Jev 使用权限。此诊断未调用推理接口。
- 增加 `AGENTD_JEV_IPV4_ONLY=1`，仅为 Jev 客户端选择 IPv4；Agent 和 Live Eval 共用 2 秒超时、零 SDK 重试的客户端构造函数。
- 用真实 `typesafe-sdk==0.7.2` 和本地 HTTP MockTransport 验证 `/v1/systemone` 请求序列化、`Choice` 字段、响应解析与 token 读取；该测试没有外部调用。
- Live CLI 在每次推理调用前向 stderr 输出 `AGENTD_LIVE_PROGRESS` JSON 行，不含 Key、任务正文或模型输出。保存这些标记有助于中断后核对尝试次数；它们不能代替服务端用量账单。

## 恢复工作

本次修改后的验证：`python -m unittest agentd.tests.test_router agentd.tests.test_router_live_eval agentd.tests.test_api -v` **10/10** 通过；修改模块 `compileall` 与 `git diff --check` 通过。历史完整 61/61 见 Phase 20，此次只重跑受影响的模块。

1. 用户替换仓库外 `jev api key.txt` 后，用 IPv4 的只读模型列表检查鉴权。
2. 先确认最初无报告尝试的服务端用量，再核算原授权下剩余次数。不得直接重跑完整 8 题，或把两次运行拼接成一次干净实验。
3. 在剩余授权范围内继续未完成案例，保留首题故障记录；若需要重跑或增加调用，应另列具体范围取得授权。
4. 当前 PR 为 [#1](https://github.com/chx739/sandboxd/pull/1)，未合并 main。
