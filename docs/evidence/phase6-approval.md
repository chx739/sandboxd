# Phase 6：DryRun 与 Operator 审批门实测证据

日期：2026-08-18

环境：WSL2 Ubuntu 24.04、单节点 kind、Kubernetes 1.35.0、gVisor、Calico

## 资源边界

- 独占临时 namespace：`sandboxd-target`；
- Deployment 初始 replicas=0；
- 批准后最多一个 gVisor pause Pod；
- Pod requests 5m CPU/8Mi，limits 50m CPU/32Mi；
- 验收前可用内存 5638Mi、swap 0Mi。

## 真实闭环

```text
DryRun: replicas remained 0 before approval
Role split: Agent approve -> 401, Operator list/approve -> 200
Approve: Deployment replicas 0 -> 1, gVisor Pod became Available
TOCTOU: resourceVersion changed -> 409, Plan stale, replicas stayed 1
Reject: Plan rejected, repeated approve -> 409, replicas stayed 1
Policy metrics: namespace=1, replicas=1, changed=1, state=1
```

## 验证点

1. 系统 namespace 提案被 400 拒绝；
2. replicas=11 被 400 拒绝；
3. 合法 Plan 返回 201、pending、`dryRunValidated=true`；
4. DryRun 后真实 Deployment 仍为 replicas=0；
5. Agent Token 调 approve 返回 401；
6. Operator Token 可 list 并 approve；
7. 批准后 Deployment replicas=1，gVisor Pod Available；
8. 提案后修改 annotation 导致 resourceVersion 改变，approve 返回 409，Plan stale；
9. reject 后再次 approve 返回 409，副本数不变；
10. 四类 bounded reason 指标均为 1。

## 纯逻辑回归

```text
go test ./internal/approval -count=20 -> PASS
go test ./...                         -> PASS
go vet ./...                          -> PASS
go build ./...                        -> PASS
```

fake client 的 DryRun Update 由测试 reactor 拦截，确保 Propose 不修改 tracker；Approve 的普通 Update 才真正落地。

## 清理结果

```text
namespace sandboxd-target: deleted
managed sandbox Pods: 0
TCP :8080 listener: 0
available memory: 5661Mi
swap used: 0Mi
```
