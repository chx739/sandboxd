# sandboxd 持续开发进度

> 本文件是跨会话进度快照。目标和边界以 `../GOAL.md` 为准；若未来继续扩展，先确认仍符合“最小、可运行、适合面试”的范围。

## 当前状态

目标范围已完成并通过最终审计。后续只做用户明确要求的修复、讲解或可选扩展，不自动扩大为生产系统。

## 已完成

- GitHub 仓库、目标锚点、跨上下文续作协议。
- WSL2 + 单节点 kind + gVisor + Calico；Pod 内真实 `Starting gVisor` 证据。
- restricted Pod 安全基线、PSA、短时 Token、只读 RBAC、NetworkPolicy 正反路径。
- Manager、filtered informer/lister、WebSocket/SPDY Exec、有界输出、超时独立清理。
- typed workqueue、target=2 预热池、JSON Patch CAS、direct fallback、Release 删除补新。
- Prometheus 低基数 acquire/pool/conflict/exec/timeout/runtime/plan denied 指标。
- Deployment scale server-side DryRun Plan、Agent/Operator 双 Token、状态机和 UID/resourceVersion 防 TOCTOU。
- approval 不变量测试连续 20 轮通过；全项目 test/vet/build 通过。
- `./hack/demo.sh` 按安全→gVisor→Exec→Pool→Approval 顺序完整实测通过。
- 所有写脚本在 cleanup 前校验 context 必须是单节点 `kind-sandboxd`。
- 完整 Demo 后：PSA=restricted、Pod=0、临时 target namespace 不存在、8080 无监听、可用内存约 5.6GiB、swap=0。
- 模块学习文档 01–10、真实踩坑总表 25 条、各阶段 evidence 和简历/面试讲法。
- Ubuntu 官方 GNU Make 4.3 已最小安装；`make build`、`make test` 和 demo target 展开均通过。

## 最终实测摘要

```text
Starting gVisor...
RBAC read=yes, write/secrets/exec=no
Network API=200, public egress=denied
Exec exit=7 preserved, timeout=504 and Pod deleted
5 concurrent claims -> 5 unique IDs, pool=2/direct=3
Pool restored idle=2, observed CAS conflicts=6
DryRun did not mutate; Agent approve=401; Operator approve=200
resourceVersion changed -> 409/stale; reject cannot reapprove
```

## 最终审计结果

- `make build`：PASS；
- `make test`：PASS；
- `go vet ./...`：PASS；
- `go mod tidy`：无额外依赖变化；
- 所有 `hack/*.sh`：`bash -n` PASS 且具备执行位；
- 非模板 Kubernetes YAML：client dry-run PASS；
- 完整真实 Demo：PASS；
- 目标资源残留、swap：0；
- 本机路径、sudo 文件名、API key/Token 敏感引用扫描：PASS；
- 本次最终里程碑提交包含完整范围与敏感信息扫描，并在提交后立即推送 GitHub 分支。

## 资源与安全约束

- kind 单 control-plane；pool=2；默认并发=5，最多 10，不做 20 并发日常测试。
- 可用内存接近 2GiB、持续 swap 或 WSL/Docker 异常时停止。
- sudo 默认禁用；密码不进入命令、脚本、日志或 Git。
- 只清理能同时确认名称、来源和目标 context 属于 sandboxd 的资源。

## 后续可选扩展（不是当前完成条件）

- in-cluster Deployment 与最小写 RBAC；
- Plan/审计持久化；
- OIDC/TLS/Token 轮转；
- Grafana/告警与正式容量测试；
- 多副本 leader election。
