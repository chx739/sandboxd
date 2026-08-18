# sandboxd

`sandboxd` 是一个面向学习和秋招面试的 Kubernetes AI Agent 执行沙箱 Demo。

项目重点不是生产化，而是通过一条能运行、能验证的最小链路理解并展示：

- gVisor 与普通容器的隔离边界；
- Pod `securityContext` 与 Pod Security Admission；
- ServiceAccount、projected token 与只读 RBAC；
- NetworkPolicy 与 CNI 的职责；
- client-go remotecommand、informer、lister 和 workqueue；
- 预热池与 JSON Patch CAS 并发认领；
- server-side dry-run 与受控写操作。

## 当前状态

项目处于环境和工程骨架阶段。实现以 [执行计划](docs/00-实现计划.md) 为准。

## 学习资料

学习文档会和对应模块一起实现，入口见 [docs/README.md](docs/README.md)。每篇文档都包含代码导读、核心原理、八股知识、常见追问和验证命令。

## 项目边界

这是单机、单进程、单租户的教学与面试 Demo，不具备生产环境要求的多租户隔离、高可用、持久化审计、凭证轮转和完整限流能力。

