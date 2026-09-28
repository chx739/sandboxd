# Phase 16：分层记忆 MVP 的本地证据

日期：2026-09-28。分支：`codex/agent-memory-rag-plan`。所有数据均为代码内合成数据；没有读取仓库外 `secrets`，没有调用外部 LLM/Jev，也没有启动 Docker、Milvus 或 ES。

## 版本和复现

- Python 3.12.14、uv 0.12.19；使用现有 `agentd/uv.lock`，本模块没有新增第三方依赖。
- Codex 源码参考：[memories README](https://github.com/openai/codex/blob/main/codex-rs/memories/README.md)。本实现仅复用两阶段写入与渐进读取思路。

```bash
UV_CACHE_DIR=/tmp/sandboxd-uv-cache uv run --project agentd --frozen -- python -m agentd.memory_eval
UV_CACHE_DIR=/tmp/sandboxd-uv-cache uv run --project agentd --frozen -- python -m unittest discover -s agentd/tests -v
```

## 本地确定性结果

`memory_eval` 使用两条合成会话、两条当前事实标签、一次过期事实更新、一次历史冲突和含伪造记忆指令的工具日志。一次执行的输出：

| 指标 | 实测 | 口径 |
|---|---:|---|
| 当前事实 Recall | 2/2 = 1.00 | 仅脚本内显式标记事实 |
| 当前事实 Precision | 2/2 = 1.00 | `MEMORY.md` 当前事实行 |
| 更新事实 | 正确 | `environment=staging` → `production` |
| 冲突组 | 1 | 保留旧值及两个来源 |
| 恶意工具日志入记忆 | 否 | 伪造 `authorization=skip_approval` 未进入整理结果 |
| 摘要占用 | 155/256 字符 | 字符预算，不是 token 预算 |
| 本地执行耗时 | 28.62 ms、90.09 ms | 两次样本，非稳定性能基准 |

全量 `unittest discover`：**45/45 通过**，包括 5 个记忆测试、既有树形 Session 和安全 Eval Replay 回归。CLI 测试实际在 `/tmp` 创建 Session，运行 `extract`、`rebuild`、`summary`、`rollout`、`forget` 五条命令。由于每条子进程会从 `/mnt/c` 导入 Python 依赖，该项测试耗时约 48 秒；不能把它当成记忆算法延迟。`memory_eval` 的耗时仅用于本地合成工作负载示例。

## 已验证的边界与未验证项

- 提取只读取成功结束 Session 的活动路径；工具节点不能成为 `GatewayExtractor` 引用来源。
- `read_memory` 在 Python Policy 校验参数后读取详情或会话摘要；启动摘要作为标注不可信的 HumanMessage 数据进入模型上下文，不替换 System Prompt。
- 文件权限测试在 WSL 原生 `/tmp` 验证目录 0700、文件 0600；没有在 `/mnt/c` 上声称相同 POSIX 权限。
- `GatewayExtractor` 只用 Fake Gateway 验证输入/来源接口，**没有真实模型提取质量或费用结果**。
- 没有 gVisor/真实集群联合运行、没有 Live LLM 提取、没有人标自然语言记忆集，也没有多进程并发正确性证据。
