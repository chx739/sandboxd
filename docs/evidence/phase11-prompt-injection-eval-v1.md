# Phase 11：Prompt Injection Eval v1 本地证据

> 日期：2026-09-01。证据类型：**确定性本地 Replay**，不是 Live LLM、kind 或 gVisor E2E。完整逐案例报告写入 `.cache/evals/`，不提交运行缓存。

## 运行边界

- 20 条内容全部为仓库内合成 Fixture，不含生产日志和真实秘密；
- 使用当前 `AgentRunner`、Pi-style Loop、Plugin Registry、Python Policy 和 `FileWorkspace`；
- Prometheus、sandboxd、Linux Host 使用只返回合成内容并记录状态变化的窄 Fake Connector；
- 串行执行，临时文件位于 `/tmp`，没有启动 kind、Docker、SSH、Prometheus 或 Alertmanager；
- 没有调用 DeepSeek 或其他外部 LLM，也没有发送项目数据。

## 命令

~~~bash
UV_CACHE_DIR=/tmp/sandboxd-uv-cache \
  uv run --project agentd --frozen python -m agentd.evals.cli lint

UV_CACHE_DIR=/tmp/sandboxd-uv-cache \
  uv run --project agentd --frozen python -m agentd.evals.cli replay \
  --output .cache/evals/prompt-injection-v1.json
~~~

## 数据集 Lint

~~~json
{
  "valid": true,
  "caseCount": 20,
  "kinds": {"clean": 4, "attack": 12, "hard-negative": 4},
  "sources": [
    "alert", "configmap", "event", "file",
    "linux_log", "podlog", "prometheus"
  ]
}
~~~

## Replay 指标

| 指标 | 结果 |
|---|---:|
| Clean Task Success | 8/8 = 100% |
| Agent Attack Success Rate | 12/12 = 100% |
| Unauthorized Side-effect Rate | 0/12 = 0% |
| Containment Rate | 12/12 = 100% |
| Over-refusal Rate | 0/8 = 0% |
| Evidence Coverage | 18/18 = 100% |
| 数据集预期契约错误 | 0 |
| Sandbox release | 20/20 |

`Agent Attack Success Rate=100%` 是测试设计：Replay 故意提出危险 Tool Call，证明拒绝边界确实承压；它不是 DeepSeek 或其他真实模型的攻击成功率。安全结果是 12 次危险尝试均被 `agent-policy` 或 `workspace-policy` 拒绝，Fake Connector 记录的未授权状态变化为 0。

## 首轮失败与修正

第一次运行得到 `Unauthorized Side-effect Rate=1/12`。定位后发现不是 Connector 状态变化，而是 ConfigMap 中的合成 canary 按设计出现在返回给已认证调用方的 `evidence` 中；Runner 把“读到证据”错误算成“对外泄露”。

修正后，canary 只检查模型生成的结论字段；外部泄露以 fake connector 的 Sink/状态变化单独统计。没有通过修改期望值隐藏失败。这个坑说明 Eval 必须先定义**授权输出通道**和**未授权 Sink**，否则指标会把正常数据流误报为攻击成功。

## 结论与限制

本证据证明确定性危险决策经过当前 Python Runtime/Workspace 时能够被遏制，并且正常证据链没有被“全部拒绝”破坏。它不证明：

- 真实模型面对 12 条注入时一定会或一定不会上当；
- Go Tool Policy、RBAC、NetworkPolicy 或 gVisor 在本轮被重新执行；
- 20 条样本足以覆盖编码、自适应、多轮、多模态攻击；
- 项目已经达到生产安全或统计显著性。

真实 gVisor/RBAC/Go Policy 证据继续以 Phase 9 全量 E2E 为准；Live 模型评估需要新的数据外发授权和单独实验协议。

