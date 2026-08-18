# sandboxd 持续开发进度

> 本文件是跨会话进度快照。每完成一个可验证步骤就更新它，避免上下文压缩后重复工作或偏移目标。目标和边界以 `../GOAL.md` 为准。

## 当前阶段

阶段 7：整理一键 Demo、最终面试问答与全量证据审计。

## 已完成

- 建立 GitHub 仓库、目标锚点和跨上下文续作协议。
- 完成 WSL2 + 单节点 kind + gVisor + Calico，并保留真实 `Starting gVisor` 证据。
- 完成 Pod 安全基线、PSA restricted、只读 RBAC、NetworkPolicy 正反路径及学习文档。
- 完成 Manager、filtered informer/lister、WebSocket/SPDY Exec、本地鉴权 HTTP API及超时清理。
- 完成 typed workqueue、目标为 2 的预热池、JSON Patch CAS、direct fallback 和 Release 补新。
- 真实五并发得到五个唯一 ID，source=pool 2/direct 3；Release 后 idle 恢复到 2。
- 完成 Prometheus acquire、pool、conflict、exec、timeout、runtime、plan denied 指标及真实验收。
- 实现内存 Plan Store，唯一动作固定为 Deployment scale，replicas 限制为 0–10，拒绝系统/沙箱 namespace。
- Agent/Operator 双 Token 必填且必须不同；Agent 只能 propose，Operator 才能 approve/reject。
- Propose 做 server-side dry-run并保存 UID/resourceVersion；Approve 前重读校验和乐观锁防 TOCTOU。
- Plan 用 pending/executing/approved/rejected/stale 状态机避免重复批准；临时 API 错误恢复 pending。
- 真实验证 Agent approve=401、Operator approve 后 replicas 0→1、版本变化=409/stale、reject 后不可重批。
- approval 核心不变量测试连续 20 轮通过；真实脚本最多一个 32Mi gVisor Pod并精确清理。
- 完成 `docs/08-DryRun与审批门.md`、阶段 6 证据和踩坑条目 19–22。

## 正在进行

- 增加按安全顺序串联各模块的一键最终 Demo。
- 编写 `docs/10-面试问答与项目讲法.md`，覆盖项目介绍、追问、边界和简历表述。
- 全量复跑/审计 README 中每个可验证结论、脚本清理和 Git 范围。

## 紧接着做

1. `make demo` 只编排已有低资源脚本，不扩大并发或副本。
2. 评估最终 Demo 是否默认复用现有集群，避免重复创建 kind/gVisor 基础设施。
3. 更新 README 的启动方式、双 Token 和完整演示输出。
4. 检查每篇模块文档都有代码入口、验证命令和一分钟项目讲法。
5. 最后运行 tests/vet/build、关键真实脚本及敏感路径扫描后推送。

## 资源与安全约束

- kind：单 control-plane 节点；预热池默认 2。
- 并发验证默认 5；资源确认充足后上限 10，不运行 20 并发日常测试。
- 可用内存接近 2 GiB、持续使用 swap 或 WSL/Docker 异常时停止测试。
- sudo 仅用于无法由普通用户完成的最小系统操作；密码不进入命令、脚本、日志或 Git。
- 只清理名称和来源都能确认属于 sandboxd 的容器、集群、网络和临时文件。

## 尚未完成

- 一键最终 Demo。
- 最终面试问答手册和简历项目描述。
- 全量证据/链接/仓库状态审计。
