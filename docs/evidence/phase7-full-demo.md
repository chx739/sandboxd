# Phase 7：完整低资源 Demo 实测证据

日期：2026-08-18

入口：`./hack/demo.sh`

## 安全前置

```text
context=kind-sandboxd
node=sandboxd-control-plane
node count=1
初始可用内存=5638MiB
初始 swap=0MiB
```

每个会修改集群的脚本都在注册 cleanup 之前调用 `require-demo-cluster.sh`。context 或节点拓扑不匹配时直接停止，避免在其他集群上执行创建或删除。

## 完整结果

```text
RBAC: get pods --all-namespaces -> yes
RBAC: create pods --namespace sandboxd-demo -> no
RBAC: get secrets --all-namespaces -> no
RBAC: create pods --subresource=exec --namespace sandboxd-demo -> no
NetworkPolicy + token + RBAC: 集群内读取 Pod -> HTTP 200
NetworkPolicy: https://example.com -> 已按预期拒绝
RuntimeClass: gvisor
[   0.000000] Starting gVisor...
HTTP auth: unauthorized -> 401
Exec success: exitCode=0，stdout/stderr 已分离
Exec failure: exitCode=7，未错误 fallback
Exec timeout: -> 504，Pod 已删除
Pool target: 2 Ready idle
Concurrent Claim: 5 requests, 5 unique IDs
Claim source: pool=2, direct=3
Release + reconcile: idle restored to 2
Metrics: runtime=gvisor, idle=2, busy=0, claim_conflicts=6
DryRun: replicas remained 0 before approval
Role split: Agent approve -> 401, Operator list/approve -> 200
Approve: Deployment replicas 0 -> 1, gVisor Pod became Available
TOCTOU: resourceVersion changed -> 409, Plan stale, replicas stayed 1
Reject: Plan rejected, repeated approve -> 409, replicas stayed 1
Policy metrics: namespace=1, replicas=1, changed=1, state=1
sandboxd 最小 Demo 全部通过
```

## 最终清理审计

```text
namespace sandboxd-demo PSA enforce=restricted
sandboxd-demo Pods=0
namespace sandboxd-target=absent
TCP :8080 listener=0
available memory=5559MiB
swap used=0MiB
```

kind 集群和持久安全清单保留，临时 workload 全部清理。

## 构建验证

```text
go test ./... -> PASS
go vet ./...  -> PASS
go build ./... -> PASS
```

最终审计最小安装 Ubuntu 官方 GNU Make 4.3（仅新增 make，414KB，未升级其他包）；`make build`、`make test` 均通过，`make -n demo` 正确展开为 `./hack/demo.sh`。完整集群演示仍使用零额外依赖入口实测，避免无意义地重复一轮 workload。
