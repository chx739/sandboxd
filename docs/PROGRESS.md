# sandboxd 持续开发进度

> 本文件是跨会话进度快照。每完成一个可验证步骤就更新它，避免上下文压缩后重复工作或偏移目标。目标和边界以 `../GOAL.md` 为准。

## 当前阶段

阶段 5：增加最小 Prometheus 指标，再实现 Deployment scale DryRun 与 Operator 审批门。

## 已完成

- 建立 GitHub 仓库、目标锚点和跨上下文续作协议。
- 完成 WSL2 + 单节点 kind + gVisor + Calico，并保留真实 `Starting gVisor` 证据。
- 完成 Pod 安全基线、PSA restricted、只读 RBAC、NetworkPolicy 正反路径及学习文档。
- 完成 Manager、filtered informer/lister、WebSocket/SPDY Exec、本地鉴权 HTTP API及超时清理。
- 实现 typed rate-limiting workqueue；所有 Pod 事件只入固定 key，单 worker幂等 Reconcile。
- Reconcile 统计 provisioning + Ready idle，维持默认 target=2；终态清理和超额缩减基于 lister 当前状态。
- Claim 使用 JSON Patch `test idle` + `replace busy` CAS；缓存冲突继续候选，池空 direct cold start。
- direct Pod 从创建时即为 busy；Release 删除而不复用，让 Reconcile 补新。
- fake client 双并发测试重复 20 轮通过；真实 kind 五并发得到五个唯一 ID，source 为 pool=2/direct=3。
- Release 后 idle 恢复到 2；脚本结束后服务停止、managed Pod=0、swap=0。
- 完成 `docs/06-Informer与控制器模式.md`、`docs/07-预热池与CAS.md` 和阶段 4 证据记录。
- `docs/11-开发踩坑与排障.md` 持续维护；新增 informer lazy init、CAS 错误包装等面试素材在对应模块文档中。

## 正在进行

- 添加 acquire、pool size、claim conflict、exec、plan denied 等最小指标。
- 暴露 `/metrics` 并在真实五并发流程中读取。
- 设计仅支持 Deployment scale 的内存 Plan Store。

## 紧接着做

1. 指标包保持简单全局 Collector，不引入 tracing 或复杂 registry。
2. 在 Manager、Pool、Exec 埋最少观测点，指标 label 保持低基数。
3. Agent Token 只能 propose；Operator Token 才能 approve/reject。
4. Propose 做 server-side dry-run，replicas 只允许 0–10，拒绝系统 namespace。
5. Plan 保存 target UID/resourceVersion，Approve 前重新校验防 TOCTOU。
6. 完成指标、审批门、双 Token 的真实验证和学习文档。

## 资源与安全约束

- kind：单 control-plane 节点；预热池默认 2。
- 并发验证默认 5；资源确认充足后上限 10，不运行 20 并发日常测试。
- 可用内存接近 2 GiB、持续使用 swap 或 WSL/Docker 异常时停止测试。
- sudo 仅用于无法由普通用户完成的最小系统操作；密码不进入命令、脚本、日志或 Git。
- 只清理名称和来源都能确认属于 sandboxd 的容器、集群、网络和临时文件。

## 尚未开始

- Prometheus 指标具体实现。
- DryRun Plan、Operator 审批门和相关 API。
- 一键最终 Demo、README 最终实测数据和最终面试问答手册。
