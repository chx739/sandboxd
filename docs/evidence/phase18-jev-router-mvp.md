# Phase 18：Jev 路由无密钥证据

时间：2026-09-28。类型：**官方 SDK 本地安装/请求形状 + Fake/Replay 确定性测试**。无 TypeSafe API 请求、无下游 LLM 调用、无秘密目录读取。

- 官方 `typesafe-sdk==0.7.2` 固定到 `agentd/uv.lock` 并安装；本地构造 `Choice` 结果含 `type=choice`、`instructions`、`criteria={economy,strong}`。`JevChoiceSource` 的假 Client 收到 `state={"request":"list pods"}` 和单个 `model_tier` Choice，返回 `economy/0.92`。
- `ModelRouter` 对高置信度使用候选；低于 0.7、非法模型等级、超时、异常分别确定性回退。异常内容不写 Trace，Trace 只记有限原因。
- Runner 假沙箱完整调用：选 `cheap` Gateway 一次、`strong` 零次，生成 Diagnosis，Trace `effectiveTier=economy`、模型 token 用量 100/50、按夹具价格估算 `$0.00006`，沙箱释放一次。没有真实 gVisor、Jev 或 LLM。
- `python -m agentd.router_eval`：8 条固定合成任务，等级准确 7/8、回退 2。固定经济成功 4/8、假设费用 `$0.00179`、假设平均下游延迟 526.25 ms；固定强 8/8、`$0.02136`、1207.5 ms；Fake 路由 8/8、`$0.01619`、1010 ms。价格、成功与耗时全部由夹具预设，不是实测模型效果；数字不含 Jev 费用与延迟。
- `python -m unittest agentd.tests.test_router -v` **4/4** 通过；完整 `unittest discover` **57/57** 通过。完整回归 398.759 秒主要落在 WSL `/mnt/c` 上的记忆 CLI 子进程启动，不能解释为 Jev 或下游模型时延。

代码和学习路径见 [36 Jev 路由学习手册](../36-Jev模型路由学习手册.md)。Live 评测仅在新的具体服务、样本量与费用上限授权后进行。
