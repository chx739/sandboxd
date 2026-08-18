# Phase 5：Prometheus 指标实测证据

日期：2026-08-18
环境：WSL2 Ubuntu 24.04、单节点 kind、Kubernetes 1.35.0、gVisor、Calico

## 静态验证

```text
go test ./...  -> PASS
go vet ./...   -> PASS
go build ./... -> PASS
```

## Pool/CAS 指标

执行 `./hack/verify-pool.sh`，资源边界为 pool=2、并发=5：

```text
可用内存: 5670 MiB
已用 swap: 0 MiB
Pool target: 2 Ready idle
Concurrent Claim: 5 requests, 5 unique IDs
Claim source: pool=2, direct=3
Release + reconcile: idle restored to 2
Metrics: runtime=gvisor, idle=2, busy=0, claim_conflicts=6
```

脚本断言了：

- `sandbox_runtime_info{runtime="gvisor"} 1`；
- `sandbox_pool_size{state="idle"} 2`；
- `sandbox_pool_size{state="busy"} 0`；
- pool/direct acquire Histogram 都至少有一个样本；
- claim conflict Counter 已暴露。

冲突数受并发调度影响，6 是本次观测值，不是固定测试期望。

## Manager/Exec 指标

执行 `./hack/verify-manager.sh`，pool=0、顺序创建两个 Pod、Exec 超时为 3 秒：

```text
HTTP auth: unauthorized -> 401
Create + informer Ready: -> 201
Exec success: exitCode=0，stdout/stderr 已分离
Exec failure: exitCode=7，未错误 fallback
Exec timeout: -> 504，Pod 已删除
Delete: -> 204，managed Pod=0
Metrics: direct acquire=2, exec=3, timeout=1
```

脚本断言：

- `sandbox_acquire_seconds_count{source="direct"} 2`；
- `sandbox_exec_seconds_count 3`；
- `sandbox_exec_timeouts_total 1`。

## 清理与资源状态

两次脚本退出后均确认：

```text
managed sandbox Pods: 0
TCP 127.0.0.1:8080 listener: 0
可用内存: 5.6 GiB 左右
swap used: 0 MiB
```
