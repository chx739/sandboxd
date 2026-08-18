# 阶段 4 验证记录：Workqueue + Pool + CAS

验证日期：2026-08-18

## 配置

```text
kind 节点: 1
gVisor RuntimeClass: gvisor
pool target: 2
并发 Claim: 5
单 Pod limit: 500m CPU / 256 MiB
```

## 快速并发回归

client-go fake tracker 上重复运行 20 轮双并发 CAS：

```text
ok github.com/chx739/sandboxd/internal/sandbox
```

fake 对 JSON Patch test 失败返回底层 `test failed`，真实 API Server 通常包装为 422 Invalid。实现将两者都视为可重试认领冲突。

## 真实 kind 结果

`hack/verify-pool.sh` 先等待两个 idle Pod 都 Ready，再同时发出五个 HTTP Claim：

```text
Pool target: 2 Ready idle
Concurrent Claim: 5 requests, 5 unique IDs
Claim source: pool=2, direct=3
Release + reconcile: idle restored to 2
```

这证明：

1. Reconcile 能把空池补到目标 2；
2. 五个并发请求没有重复 ID；
3. 两个预热 Pod 被 CAS 认领，剩余请求走 direct fallback；
4. 五个 busy Pod 删除后，Reconcile 再次恢复两个 idle；
5. lister 的最终一致性没有破坏认领唯一性。

## 清理和资源

```text
验收完成后 WSL 可用内存: 约 6.2 GiB
swap: 0
8080 listener: none
managed Pod: 0
```

脚本启动前要求 managed Pod=0；退出时先停止 Pool worker，再使用精确 managed label 清理本次创建的 Pod。未使用 sudo。

## 复现

```bash
./hack/check-resources.sh
./hack/verify-pool.sh
```
