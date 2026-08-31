# 学习文档索引

文档按实现顺序编写。每完成一个模块，同步完成对应学习文档，避免代码写完后再补无法对应实际实现的“八股总结”。

| 顺序 | 文档 | 对应实现 | 状态 |
|---:|---|---|---|
| 0 | [实现计划](00-实现计划.md) | 全项目 | 已完成 |
| 1 | [gVisor 与容器隔离](01-gVisor与容器隔离.md) | WSL、runsc、RuntimeClass | 已完成并实测 |
| 2 | [Pod 安全基线](02-Pod安全基线.md) | `internal/sandbox/spec.go` | 已完成并测试 |
| 3 | [ServiceAccount 与 RBAC](03-ServiceAccount与RBAC.md) | `deploy/rbac.yaml` | 已完成并实测 |
| 4 | [NetworkPolicy 与 CNI](04-NetworkPolicy与CNI.md) | Calico、NetworkPolicy | 已完成并实测 |
| 5 | [client-go 与 Exec](05-client-go与Exec.md) | Manager、remotecommand、基础 API | 已完成并实测 |
| 6 | [Informer 与控制器模式](06-Informer与控制器模式.md) | informer、lister、workqueue | 已完成并实测 |
| 7 | [预热池与 CAS](07-预热池与CAS.md) | Pool、JSON Patch | 已完成并发实测 |
| 8 | [DryRun 与 Operator 审批门](08-DryRun与审批门.md) | Plan Store、双 Token、TOCTOU | 已完成并实测 |
| 9 | [Prometheus 指标与轻量性能验证](09-指标与性能测试.md) | metrics、真实低并发验收 | 已完成并实测 |
| 10 | [秋招面试问答与项目讲法](10-面试问答与项目讲法.md) | 总结、追问、简历、Demo | 已完成并实测 |
| 11 | [开发踩坑与排障](11-开发踩坑与排障.md) | 全项目真实问题 | 持续维护 |
| 12 | [Agent 层实现计划](12-Agent层实现计划.md) | Phase 2 权威规格与边界 | Replay 与 DeepSeek Live 均已实测 |
| 13 | [项目学习路径](13-项目学习路径.md) | 文档阅读顺序、破坏实验、复习检验 | 已完成 |
| 14 | [LangGraph 告警诊断学习手册](14-LangGraph告警诊断学习手册.md) | agentd、外部告警、三个工具、Live/Replay | Replay 与 Live 均已实测 |
| 15 | [Agent 安全面试问答](15-Agent安全面试问答.md) | 32 个高频追问、边界与扩展 | 已完成 |
| 16 | [Pi-inspired Agent 内核优化计划](16-Pi-inspired-Agent内核优化计划.md) | Phase 2.1 双通道、事件、上下文、取消 | 已完成（历史计划） |
| 17 | [Pi-style 安全可插拔 Agent 实现计划](17-Pi-style安全可插拔Agent实现计划.md) | Phase 3 权威范围与恢复锚点 | 已完成 |
| 18 | [Pi-style Agent Runtime 学习手册](18-Pi-style-Agent-Runtime学习手册.md) | 双层 Loop、Session、插件、身份 | 当前主学习文档 |
| 19 | [Pi-style 运维 Agent 面试问答](19-Pi-style运维Agent面试问答.md) | 30 个 Phase 3 高频追问 | 当前自测文档 |
| 20 | [Linux Host 与文件工具实现计划](20-Linux主机与原生文件工具实现计划.md) | Phase 4 权威范围、接口与恢复锚点 | 已实现 |
| 21 | [Linux SSH Connector 学习手册](21-Linux-SSH-Connector学习手册.md) | strict host key、固定 argv、forced-command | 当前主学习文档 |
| 22 | [Agent 原生文件工具学习手册](22-Agent原生文件工具学习手册.md) | task 工作区、路径、CAS、原子写、脱敏 | 当前主学习文档 |
| 23 | [Linux 与文件工具面试问答](23-Linux与文件工具面试问答.md) | 20 个 Phase 4 高频追问与 90 秒讲法 | 当前自测文档 |

> 12、14、15 保留 Phase 2/LangGraph 的历史实现与真实证据；当前代码以 17、18、19 为准。
> Phase 4 的 SSH 与文件能力以 20–23 为准；SSH 不经过 gVisor，不能混淆信任边界。
> 13 是**学习方法**不是模块文档，不遵循下面的固定结构。

## 每篇文档的固定结构

1. 这个模块解决什么问题；
2. 项目里的最小实现；
3. 代码阅读顺序；
4. 必须掌握的基础知识；
5. 为什么采用当前方案；
6. 考虑过但没有采用的方案；
7. 常见错误和本项目踩坑；
8. 面试高频问题与回答思路；
9. 自己动手验证的命令；
10. 一分钟项目讲法。

学习要求不是背答案，而是能从实际代码和命令输出解释每一个结论。
