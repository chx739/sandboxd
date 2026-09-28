# TypeSafe Jev 模型路由 MVP

## 1. 一分钟讲法

每个 Agent Task 开始前，Jev 的 `Choice` 只判断这次运维请求应走 `economy` 还是 `strong` 两个固定模型等级。应用程序用静态表把等级映射到已配置的 `LiveModelGateway`；Jev 不提供任意模型名，更不能改变 Plugin、Policy 或审批权限。低置信度、非法等级、超时与 SDK 报错都回退到 `strong`。一个 Task 只选一次，下游模型使用次数和费用估算进入 Trace。

```text
Alert summary -> Jev Choice(economy | strong) -> 阈值/合法性/超时判断
                                           -> 固定 Gateway -> 当前 Task 的 Agent Loop
                                           -> Trace: 决策/回退/用量/估算费用
```

## 2. 代码阅读顺序

1. `agentd/app/router.py`：`JevChoiceSource` 用官方 Python SDK 的 `AsyncTypeSafeClient.system_one` + `Choice`；`ModelRouter` 实施静态等级、0.7 阈值、2 秒超时及默认回退。`FakeChoiceSource` 为确定性测评。
2. `agentd/app/runner.py`：领取沙箱前只调用一次 `choose`，之后 `new_session` 和 Trace 都使用被选 Gateway。路由结果不会再参与工具校验。
3. `agentd/app/config.py`、`main.py`：默认 `AGENTD_ROUTER_MODE=off`；`jev` 模式仅在 Live、两个下游模型和 TypeSafe Key 都明确配置后创建。`typesafe-sdk==0.7.2` 是 `jev` 可选依赖。
4. `agentd/router_eval.py` 与 `testdata/router-v1.jsonl`：8 条纯合成 Fake/Replay 接线夹具，比较固定经济、固定强和路由三组的预设成功率、时延与费用算术。
5. `agentd/tests/test_router.py`：SDK 请求形状的假 Client、四类回退、单 Task 一次绑定及 Trace 成本记录。

官方 [Intent routing](https://docs.typesafe.ai/patterns/intent-routing) 把 Jev 放在处理器前做分类；[Choice](https://docs.typesafe.ai/primitives/choice) 返回选项与置信度；[Python SDK](https://docs.typesafe.ai/sdk/python) 支持 async `system_one`。这些资料说明 API 形状，项目的等级策略、回退和权限仍由本项目代码负责。

## 3. 无密钥复现

```bash
uv sync --project agentd --frozen --extra jev
uv run --project agentd --frozen --extra jev python -m agentd.router_eval
uv run --project agentd --frozen --extra jev python -m unittest agentd.tests.test_router -v
```

`router_eval` 不调用 Jev 或任何下游 LLM，也没有读取仓库外 Key。它的 8 条任务、成功标记、token 数、模型延迟和每百万 token 单价都是**人为固定的教学夹具**。当前输出：等级匹配 7/8、回退 2 次；固定经济 4/8 成功、假设费用 `$0.00179`；固定强 8/8、`$0.02136`；Fake 路由 8/8、`$0.01619`。这只能证明路由/回退/统计公式，**不能证明 Jev 的真实分类准确率、真实任务成功率、实际延迟或节省费用**。

## 4. Live 配置与待授权项

应用支持以下配置，但本阶段没有向 TypeSafe 或任何外部 LLM 发送请求：

```text
AGENTD_LLM_MODE=live
AGENTD_ROUTER_MODE=jev
AGENTD_ROUTER_ECONOMY_MODEL=<已配置网关上的经济模型>
AGENTD_ROUTER_STRONG_MODEL=<同一网关上的强模型>
TYPESAFE_API_KEY=<从仓库外安全注入>
AGENTD_ROUTER_PRICES_JSON={"economy":{"inputUsdPerMillion":... ,"outputUsdPerMillion":...},"strong":{...}}
```

下游模型沿用项目已有 `AGENTD_LLM_BASE_URL`、`AGENTD_LLM_API_KEY` 和 Tool Calling Gateway；这些模型名、能力、单价需要在 Live 前实测或核实。价格表可不填，此时 Trace 的 `estimatedModelCostUsd` 为 `null`，不能解释为 0。Jev 自身费用也记为 `routerCostUsd=null`，直到有可核实的报价/用量；合成夹具的 `$` 不进入真实账单口径。SDK 的 `TYPESAFE_API_KEY` 只从进程环境传入，不进入 Session、Trace 或 Git。

Live 测评需要用户新的明确授权，单列 TypeSafe Jev 服务、下游经济/强模型、任务样本量和费用上限。建议先在合成运维问题上做小样本，按**同一批**任务比较固定经济、固定强、Jev 路由的完成质量、token 费用、p50/p95 和错误路由；即便使用真实 Jev，也不能把 Fake 结果当成测评质量。

## 5. 常见追问

- **为什么不直接让 LLM 自报模型？** 模型选择由 Jev 输出固定等级，应用白名单验证并映射到部署者配置的 Gateway；不能从自然语言拼接 URL/模型名。
- **为什么低置信度用 strong？** 此运维 Demo 优先保持复杂诊断质量；这是一条可审查策略，不代表 strong 自动安全。敏感工具仍由 Policy 与 sandboxd 授权。
- **路由失败后会不会重复执行工具？** 路由在领取沙箱和 Agent Loop 前，只回退选择模型；没有历史工具副作用可重放。
- **是否节约了钱？** 目前只有合成费用公式，真实收益还要包含 Jev 调用本身、两个下游模型的实际 token/报价和质量回归。
- **需要 LangGraph 吗？** 这里只是在既有 Runner 前增加一次 `Choice`，手写函数比新图状态更容易审计；未来复杂异步 checkpoint 才值得评估迁移。
