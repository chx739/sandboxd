# sandboxd 持续开发进度

> 本文件是跨会话进度快照。每完成一个可验证步骤就更新它，避免上下文压缩后重复工作或偏移目标。目标和边界以 `../GOAL.md` 为准。

## 当前阶段

阶段 6：实现只支持 Deployment scale 的 server-side DryRun Plan 与双 Token Operator 审批门。

## 已完成

- 建立 GitHub 仓库、目标锚点和跨上下文续作协议。
- 完成 WSL2 + 单节点 kind + gVisor + Calico，并保留真实 `Starting gVisor` 证据。
- 完成 Pod 安全基线、PSA restricted、只读 RBAC、NetworkPolicy 正反路径及学习文档。
- 完成 Manager、filtered informer/lister、WebSocket/SPDY Exec、本地鉴权 HTTP API及超时清理。
- 实现 typed rate-limiting workqueue；所有 Pod 事件只入固定 key，单 worker 幂等 Reconcile。
- Reconcile 维持默认 pool target=2；Claim 使用 JSON Patch CAS，池空 direct cold start，Release 删除补新。
- fake client 并发测试重复 20 轮通过；真实 kind 五并发得到五个唯一 ID，source 为 pool=2/direct=3。
- 增加 Prometheus acquire、pool size、claim conflict、exec、timeout、plan denied 和 runtime 指标，暴露 `/metrics`。
- 真实五并发观察到 6 次 CAS 冲突；Gauge 最终收敛到 idle=2/busy=0。
- 真实 Exec 流程得到 direct acquire=2、exec=3、timeout=1；超时 Pod 正确删除。
- 指标验收后服务停止、managed Pod=0、可用内存约 5.6 GiB、swap=0。
- 完成 `docs/09-指标与性能测试.md` 和阶段 5 证据；踩坑总表新增最终一致 Gauge、非确定冲突数和 Go 依赖图条目。

## 正在进行

- 设计仅支持 Deployment scale 的内存 Plan Store。
- 拆分 Agent Token 与 Operator Token 权限。
- 定义 namespace denylist、replicas 0–10、UID/resourceVersion 防 TOCTOU 规则。

## 紧接着做

1. Agent Token 只能 propose/list；Operator Token 才能 approve/reject。
2. Propose 获取 Deployment 后执行 server-side dry-run，replicas 只允许 0–10，拒绝系统 namespace。
3. Plan 保存 target UID/resourceVersion、before/after 和状态，暂存内存，不引入数据库。
4. Approve 前重新读取并校验对象，审核后目标变化则拒绝执行。
5. 用专用低资源 Deployment 完成 propose、越权拒绝、Operator 执行和 TOCTOU 拒绝实测。
6. 编写 `08-DryRun与审批门.md`，继续把真实坑加入 `11-开发踩坑与排障.md`。

## 资源与安全约束

- kind：单 control-plane 节点；预热池默认 2。
- 并发验证默认 5；资源确认充足后上限 10，不运行 20 并发日常测试。
- 可用内存接近 2 GiB、持续使用 swap 或 WSL/Docker 异常时停止测试。
- sudo 仅用于无法由普通用户完成的最小系统操作；密码不进入命令、脚本、日志或 Git。
- 只清理名称和来源都能确认属于 sandboxd 的容器、集群、网络和临时文件。

## 尚未开始

- DryRun Plan、Operator 审批门和双 Token 真实验证。
- 一键最终 Demo、README 最终实测数据和最终面试问答手册。
