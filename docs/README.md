# sandboxd 文档导航

> 这里是项目学习的唯一入口。文档按“当前真相 → 代码与模块 → 面试表达 → 历史与证据”分层，而不是按开发时间顺序通读。

## 20 分钟快速了解

这个入口只用于“先判断项目在做什么”，不是完整学习计划：

1. 读 [项目 README](../README.md) 的架构图、能力状态和项目边界；
2. 读 [24 项目全景](24-项目全景与心智模型.md) 的第 1–8 节，画出信任边界；
3. 扫描 [10 项目设计 FAQ](10-面试问答与项目讲法.md) 的四条主线和生产化差距。

如果要真正学习代码，不在这里继续追加文档，直接进入 [13 项目学习路径](13-项目学习路径.md) 的“一天压缩版”或“四天完整版”。两个入口面向不同目标，不再维护互相竞争的“时间不够阅读清单”。

## 系统学习的 7 个入口

| 顺序 | 文档 | 读完应该得到什么 |
|---:|---|---|
| 1 | [项目 README](../README.md) | 项目解决什么问题、完整链路和已验证能力 |
| 2 | [项目全景与心智模型](24-项目全景与心智模型.md) | 信任边界、读写分流、身份和数据流 |
| 3 | [项目学习路径](13-项目学习路径.md) | 4 天完整版和 1 天压缩版学习安排 |
| 4 | [代码导读与模块地图](25-代码导读与模块地图.md) | 从入口沿一条告警读到 gVisor、审批和清理 |
| 5 | [Agent 通用八股知识地图](26-Agent八股知识地图.md) | ReAct、Tool Calling、Context、Session、Plugin、安全和 Eval |
| 6 | [项目设计 FAQ](10-面试问答与项目讲法.md) | 项目级完整答案的唯一入口、真实坑、边界和自测题 |
| 7 | [简历与面试表达手册](27-简历与面试表达手册.md) | 简历三行、30 秒/1 分钟/3 分钟讲法和 STAR 素材 |

## 当前实现的模块学习文档

### A. Kubernetes 沙箱与可信执行层

| 模块 | 学习文档 | 关键代码/配置 |
|---|---|---|
| 运行时隔离 | [01 gVisor 与容器隔离](01-gVisor与容器隔离.md) | `deploy/runtimeclass.yaml`、`hack/create-cluster.sh` |
| Pod 基线 | [02 Pod 安全基线](02-Pod安全基线.md) | `internal/sandbox/spec.go` |
| 身份权限 | [03 ServiceAccount 与 RBAC](03-ServiceAccount与RBAC.md) | `deploy/rbac.yaml` |
| 网络边界 | [04 NetworkPolicy 与 CNI](04-NetworkPolicy与CNI.md) | `deploy/networkpolicy.yaml.tmpl` |
| 生命周期与 Exec | [05 client-go 与 Exec](05-client-go与Exec.md) | `manager.go`、`exec.go`、HTTP API |
| 控制器模式 | [06 Informer 与控制器](06-Informer与控制器模式.md) | informer/lister/workqueue |
| 并发与预热 | [07 预热池与 CAS](07-预热池与CAS.md) | `internal/sandbox/pool.go` |
| 写操作治理 | [08 DryRun 与审批门](08-DryRun与审批门.md) | `internal/approval/service.go` |
| 可观测性 | [09 指标与轻量性能验证](09-指标与性能测试.md) | `internal/metrics/metrics.go` |

### B. 当前 Agent Runtime 与工具层

| 模块 | 主学习文档 | 重点 |
|---|---|---|
| Agent Runtime | [18 Pi-style Agent Runtime](18-Pi-style-Agent-Runtime学习手册.md) | 双层循环、steer/follow-up/cancel、Session、插件 |
| Linux Connector | [21 Linux SSH Connector](21-Linux-SSH-Connector学习手册.md) | 固定 argv、Host Key、低权限、forced-command |
| 文件工具 | [22 Agent 原生文件工具](22-Agent原生文件工具学习手册.md) | task 工作区、路径、symlink、CAS、原子替换 |
| 安全测评 | [29 Prompt Injection Eval](29-Prompt-Injection-Eval学习手册.md) | 20 条 JSONL、Replay/Live、六个指标、行为/边界分层 |

当前代码已经不依赖 LangGraph。`14` 只用于理解 Phase 2 的架构演进和真实 Live/Replay 证据，不能作为当前代码说明。

## 问题、答案与概念怎样分层

| 文档 | 唯一职责 |
|---|---|
| [10 项目设计 FAQ](10-面试问答与项目讲法.md) | 当前项目级问题的完整答案唯一来源 |
| [15 Phase 2 安全问题索引](15-Agent安全面试问答.md) | 只列安全与历史问题，指向 `10/14/24/25/evidence` |
| [19 Runtime 问题索引](19-Pi-style运维Agent面试问答.md) | 只列 Loop、Session、Plugin 问题，指向 `10/18/24/25` |
| [23 Linux/File 问题索引](23-Linux与文件工具面试问答.md) | 只列 SSH 与文件问题，指向 `10/21/22/24/25` |
| [26 Agent 通用八股](26-Agent八股知识地图.md) | 脱离项目可复用的概念与方案比较，不维护第二套项目答案 |
| [27 简历与表达手册](27-简历与面试表达手册.md) | 投递、项目介绍、STAR 深挖和诚信边界 |

正确用法是从 `15/19/23` 抽题，先口述，再到 `10` 核对项目口径、到模块手册核对原理、到 `24/25` 核对事实和代码。不要在索引文件里重新补完整答案。

## 历史计划与实现演进

这些文件用于追溯设计决策，第一次学习不要通读：

| 文档 | 性质 |
|---|---|
| [00 全项目最小实现计划](00-实现计划.md) | Phase 1 总览和初始约束 |
| [12 Phase 2 Agent 实现计划](12-Agent层实现计划.md) | 已完成的 LangGraph 历史规格 |
| [14 Phase 2 LangGraph 历史实现与证据](14-Phase2-LangGraph历史实现与证据.md) | 历史实现和 Live/Replay 证据，文件名直接标明非当前 Runtime |
| [16 Phase 2.1 内核优化计划](16-Pi-inspired-Agent内核优化计划.md) | ToolResult、事件、Context、取消的来源 |
| [17 Phase 3 实现计划](17-Pi-style安全可插拔Agent实现计划.md) | 当前双层循环与 Session 的设计来源 |
| [20 Phase 4 实现计划](20-Linux主机与原生文件工具实现计划.md) | SSH 与文件工具的范围和验收顺序 |
| [28 Phase 5 Eval v1 实现计划](28-Prompt-Injection-Eval-v1实现计划.md) | 20 条测评集、Runner、Scorer 和验收边界 |
| [PROGRESS](PROGRESS.md) | 给后续开发者/LLM 的完成记录，不是学习教材 |

项目的演进主线可以面试时简述为：

```text
LangGraph 显式图
  -> 抽出 ToolResult / Event / Context / Cleanup
  -> 手写 Pi-style 双层循环，删除 LangGraph 依赖
  -> 加入受信任插件、Session 和交互控制
  -> 接入受限 SSH 与 task 文件工作区
```

## 证据与排障

- [48 条真实开发坑](11-开发踩坑与排障.md)：优先挑 5 条能讲完整“现象—根因—修复—价值”的。
- [evidence/](evidence/)：人工脱敏的真实运行证据；Replay 与 Live 必须区分。
- [phase8 Agent 告警证据](evidence/phase8-agent-alert.md)：完整外部告警、注入、策略、审批链路。
- [phase9 main 全量回归](evidence/phase9-full-regression.md)：2026-09-01 静态、Phase 1–4 E2E、残留与 Live 边界。
- [phase10 DeepSeek 单次 Live E2E](evidence/phase10-deepseek-live-e2e.md)：Task 成功但模型跳过 Prometheus，严格 E2E 失败；未重试。
- [phase11 Prompt Injection Eval v1](evidence/phase11-prompt-injection-eval-v1.md)：20 条本地 Replay、六指标与首轮误报修正。
- [学习实验台账](evidence/learning-experiments.md)：区分“已执行实验、已有等价证据、尚未执行”，不把预测写成实测。

## 学习纪律

每个模块都用同一个闭环：

```text
先画数据流
  -> 读入口和核心函数
  -> 用自己的话解释“为什么”
  -> 跑最小正反验证
  -> 讲一个真实坑
  -> 回答生产化还缺什么
```

能完成这六步，才算真正掌握；只读完文档不算。
