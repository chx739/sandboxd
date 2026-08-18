# 模块 07：预热池与 JSON Patch CAS

## 这个模块解决什么问题

直接创建 gVisor Pod 要经历调度、镜像和运行时启动。预热池提前维持少量 Ready idle Pod，让请求可以快速认领。同时多个请求可能看到同一份旧缓存，必须保证同一个 Pod 只分配给一个调用方。

## 项目里的最小实现

- target 默认为 2；
- Reconcile 统计所有非终态 idle，包括 Pending provisioning；
- idle 少于 target 时调用 `Manager.CreateIdle`，多于 target 时删除最老的多余 Pod；
- Claim 只选择 Ready idle；
- 用 JSON Patch `test idle` + `replace busy` 原子认领；
- 候选冲突后继续下一个，全部失败则 direct cold start；
- direct Pod 从创建时就是 busy；
- Release 直接删除，让 Reconcile 补新 Pod，不复用旧环境。

## 为什么 provisioning 也要计入容量

假设 target=2，两个 idle Pod 正在拉镜像但还没 Ready。如果只统计 Ready，下一次事件又会创建两个，反复对账后池会膨胀。容量判断计入 Pending idle，Claim 候选才要求 Ready，这两个判断解决不同问题。

## JSON Patch CAS

核心 Patch：

```json
[
  {"op":"test","path":"/metadata/labels/sandbox.io~1state","value":"idle"},
  {"op":"replace","path":"/metadata/labels/sandbox.io~1state","value":"busy"}
]
```

API Server 在同一个原子 Patch 中先检查 label 仍为 idle，再改成 busy。两个请求即使都从旧 lister 看到 idle，也只有一个 test 成功。

JSON Pointer 中 `/` 是路径分隔符，因此 label key `sandbox.io/state` 必须写成 `sandbox.io~1state`。漏掉转义会查找错误路径。

## 为什么不用 merge patch

Merge Patch 是 last-write-wins。两个请求都把 state 写为 busy，两个请求都可能得到成功响应并返回同一 Pod，无法表达“仅当旧值仍为 idle 才写”。

## 与 resourceVersion Update 对比

另一种做法是 Get 对象、修改 label、携带 resourceVersion Update；对象任何字段变化都可能产生 409 Conflict。JSON Patch test 只检查关心的 state 字段，冲突粒度更精确。

两者本质都是 optimistic concurrency control：不提前加分布式锁，由存储层在提交时检测前提是否仍成立。

## 为什么池空时 direct fallback

可用性优先于固定命中率。池中两个 Pod 被占用后，后续请求直接冷启动 busy Pod，不等待 Reconcile 补池。真实五并发结果是 pool=2、direct=3，所有请求都成功且 ID 唯一。

## 为什么 Release 不复用

用户命令可能留下文件、后台进程、环境变量或缓存。尝试“清空”很难证明彻底；对面试 Demo，删除并补新 Pod 的状态模型更简单可靠。代价是额外启动开销，文档明确承认。

## 真实踩坑：fake client 的 Patch 错误包装

真实 API Server 的 JSON Patch test 失败通常返回 Kubernetes 422 Invalid；client-go fake object tracker 直接返回底层 `test failed` 错误。最初只用 `apierrors.IsInvalid`，并发单测因此把正常 CAS 冲突当成致命错误。

实现兼容 Invalid、Conflict 和 fake 的 `test failed`，随后并发测试重复 20 轮通过。最终正确性仍以真实 kind 五并发结果为主，fake 只提供快速回归。

## 考虑过但没有采用的方案

- 全局 mutex：只保护单进程，多副本或外部修改时失效。
- Lease 对象：可以做分布式锁，但为一个 label 状态增加额外资源和清理成本。
- 数据库存储分配：项目无状态目标不需要数据库。
- Release 后清理并复用：难以可靠清除不可信代码留下的全部状态。
- target 动态扩缩：面试 Demo 固定 2 更容易复现和解释。

## 常见错误

- direct Pod 先标 idle，产生短暂双重认领窗口；
- 缓存中看到 idle 就直接返回，没有服务端 CAS；
- JSON Pointer 忘记把 `/` 转义为 `~1`；
- 只统计 Ready 导致 provisioning 期间过度补池；
- Release 把 Pod label 改回 idle，复用污染环境；
- 五并发返回五个响应，却忘记检查 ID 是否唯一。

## 面试高频问答

**问：Lister 数据旧了怎么办？**

答：旧数据最多让请求多尝试一次已被占用的候选。API Server Patch test 会拒绝，Claim 再尝试其他 Pod或 direct fallback，不会重复分配。

**问：这是悲观锁还是乐观锁？**

答：是乐观并发控制。读取时不加锁，提交时校验 state 仍是 idle；冲突说明别人先成功，当前请求换候选。

**问：为什么只有一个 Pool worker还需要 CAS？**

答：worker 只负责补池；HTTP Claim 请求是并发执行的，而且缓存可能陈旧。CAS保护的是多个 Claim 对同一 Pod 的竞争，与 Reconcile worker 数量无关。

## 实测结果

```text
Pool target: 2 Ready idle
Concurrent Claim: 5 requests, 5 unique IDs
Claim source: pool=2, direct=3
Release + reconcile: idle restored to 2
```

测试前后 swap 为 0，结束后服务停止、managed Pod 清理为 0。

## 验证命令

```bash
go test ./internal/sandbox -run TestConcurrentClaimReturnsDifferentPods -count=20
./hack/verify-pool.sh
```

## 一分钟项目讲法

预热池用 informer 和单 worker维持两个 idle Pod。容量统计包括 Pending provisioning，避免镜像拉取期间重复补池；Claim 只选 Ready。多个请求可能从旧缓存看到同一 Pod，所以我用 JSON Patch 的 test+replace 做服务端 CAS，只有 state 仍是 idle 的请求能改成 busy。池空时直接冷启动，Release 不复用而是删除补新。真实五并发得到五个唯一 ID，其中两个命中池、三个 direct，释放后池恢复为两个。
