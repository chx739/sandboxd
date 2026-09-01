# sandboxd 文档导航

> 这里是项目学习的唯一入口。文档按“当前真相 → 代码与模块 → 面试表达 → 历史与证据”分层，而不是按开发时间顺序通读。

## 第一次学习只读这 7 篇

| 顺序 | 文档 | 读完应该得到什么 |
|---:|---|---|
| 1 | [项目 README](../README.md) | 项目解决什么问题、完整链路和已验证能力 |
| 2 | [项目全景与心智模型](24-项目全景与心智模型.md) | 信任边界、读写分流、身份和数据流 |
| 3 | [项目学习路径](13-项目学习路径.md) | 4 天完整版和 1 天压缩版学习安排 |
| 4 | [代码导读与模块地图](25-代码导读与模块地图.md) | 从入口沿一条告警读到 gVisor、审批和清理 |
| 5 | [Agent 八股知识地图](26-Agent八股知识地图.md) | ReAct、Tool Calling、Context、Session、Plugin、安全和 Eval |
| 6 | [面试总纲与项目讲法](10-面试问答与项目讲法.md) | 项目级追问、真实坑、边界和自测题 |
| 7 | [简历与面试表达手册](27-简历与面试表达手册.md) | 简历三行、30 秒/1 分钟/3 分钟讲法和 STAR 素材 |

如果时间很少，只读 1、2、6、7；不要先通读实现计划和 `PROGRESS.md`。

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

当前代码已经不依赖 LangGraph。`14` 只用于理解 Phase 2 的架构演进和真实 Live/Replay 证据，不能作为当前代码说明。

## 面试题库怎样使用

| 文档 | 适合场景 |
|---|---|
| [10 面试总纲](10-面试问答与项目讲法.md) | 项目整体介绍、Kubernetes/Go/Agent 综合面 |
| [15 Agent 安全面试问答](15-Agent安全面试问答.md) | Prompt Injection、策略分层、Live/Replay；含 Phase 2 历史表述 |
| [19 Pi-style Runtime 问答](19-Pi-style运维Agent面试问答.md) | Loop、Session、身份、Plugin、取消语义 |
| [23 Linux 与文件工具问答](23-Linux与文件工具面试问答.md) | SSH、命令注入、路径穿越、原子写、TOCTOU |
| [26 Agent 八股知识地图](26-Agent八股知识地图.md) | 脱离项目也能回答的 Agent 基础与方案取舍 |
| [27 简历与表达手册](27-简历与面试表达手册.md) | 投递、项目介绍、STAR 深挖和诚信边界 |

正确用法是先遮住答案口述，再回代码核对；不是背诵整篇。

## 历史计划与实现演进

这些文件用于追溯设计决策，第一次学习不要通读：

| 文档 | 性质 |
|---|---|
| [00 全项目最小实现计划](00-实现计划.md) | Phase 1 总览和初始约束 |
| [12 Phase 2 Agent 实现计划](12-Agent层实现计划.md) | 已完成的 LangGraph 历史规格 |
| [14 LangGraph 学习手册](14-LangGraph告警诊断学习手册.md) | 历史实现和 Live/Replay 证据 |
| [16 Phase 2.1 内核优化计划](16-Pi-inspired-Agent内核优化计划.md) | ToolResult、事件、Context、取消的来源 |
| [17 Phase 3 实现计划](17-Pi-style安全可插拔Agent实现计划.md) | 当前双层循环与 Session 的设计来源 |
| [20 Phase 4 实现计划](20-Linux主机与原生文件工具实现计划.md) | SSH 与文件工具的范围和验收顺序 |
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

- [47 条真实开发坑](11-开发踩坑与排障.md)：优先挑 5 条能讲完整“现象—根因—修复—价值”的。
- [evidence/](evidence/)：人工脱敏的真实运行证据；Replay 与 Live 必须区分。
- [phase8 Agent 告警证据](evidence/phase8-agent-alert.md)：完整外部告警、注入、策略、审批链路。

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
