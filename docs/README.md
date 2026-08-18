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
| 6 | `06-Informer与控制器模式.md` | informer、lister、workqueue | 进行中 |
| 7 | `07-预热池与CAS.md` | Pool、JSON Patch | 进行中 |
| 8 | `08-DryRun与审批门.md` | Gate、双 Token | 待实现 |
| 9 | `09-指标与性能测试.md` | Prometheus、bench | 待实现 |
| 10 | `10-面试问答与项目讲法.md` | Demo、README | 待实现 |
| 11 | [开发踩坑与排障](11-开发踩坑与排障.md) | 全项目真实问题 | 持续维护 |

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
