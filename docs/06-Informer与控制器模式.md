# 模块 06：Informer、Workqueue 与控制器模式

## 这个模块解决什么问题

预热池要持续回答“当前有多少 idle Pod，是否需要补充或缩减”。如果每次事件都直接查询 API Server 并修改资源，事件风暴会导致重复操作和高 QPS。本模块用一个共享 informer、本地 lister 和固定 key workqueue，把大量边沿事件收敛成少量幂等对账。

## 项目里的最小实现

- main 只创建一个 filtered Pod informer，只 watch `sandbox.io/managed-by=sandboxd`；
- Manager 和 Pool 共用同一个 lister；
- Pool handler 收到 Add/Update/Delete 时只执行 `queue.Add("pool")`；
- typed rate-limiting queue 对固定 key 去重；
- 单 worker 调用 `Reconcile`；
- Reconcile 只依据 lister 当前快照计算目标与实际差额。

## 代码阅读顺序

1. `cmd/sandboxd/main.go`：informer 实例化、Start、cache sync、依赖注入；
2. `internal/sandbox/manager.go`：WaitReady 如何读 lister；
3. `internal/sandbox/pool.go`：handler、queue、worker、Reconcile；
4. `hack/verify-pool.sh`：真实 target 与并发验收。

## List/Watch 与本地缓存

Informer 首先 List 当前对象，随后从 resourceVersion 开始 Watch 增量变化。Store/Indexer 保存本地对象快照，Lister 只读这份缓存。

收益：

- Manager/Pool 多次读取不增加 API Server QPS；
- Watch 事件驱动下一次对账；
- 同一 informer 可被多个模块共享。

代价是最终一致性：API Server Patch 成功后，lister 可能短暂仍显示旧 label。正确性不能依赖缓存立即更新，CAS 必须交给 API Server 判定。

## 为什么 handler 不做业务逻辑

事件对象只代表“某个时刻发生过变化”，handler 执行时对象可能已经再次变化。若在 handler 里直接补 Pod，Add/Update/Delete 密集到达时很容易重复创建。

本项目 handler 只入固定 key。worker 取出 key 后重新从 lister 读取最新全局状态，这叫 level-based reconciliation：关注当前状态与目标的差，而不是逐个重放历史事件。

## Workqueue 为什么能防抖

同一个 key 已在队列或正在处理时，再次 Add 会被标记 dirty，但不会并发交给另一个 worker。处理完成后如果期间又 dirty，最多再入队一次。

因此一秒内 100 次 Pod 更新不等于执行 100 次并发 Reconcile。项目进一步只开一个 worker，避免同一个小池被并发补充。

失败时使用 rate-limited requeue，成功后必须 `Forget`，否则 rate limiter 会永久保留失败次数并持续增加退避。

## Resync 的含义

main 设置 30 分钟 resync。Resync 不是重新请求 API Server 全量 List，而是把本地缓存对象重新送给 handler，触发兜底对账。它用于修复事件处理偶发失败或外部修改，不是业务轮询定时器。

## 真实踩坑：lazy initialization

typed informer wrapper 不一定在获取 `Pods()` 时就注册 shared informer；第一次 `.Informer()` 才真正实例化。若先 `factory.Start()` 再第一次 `.Informer()`，factory 启动时没有 informer，`WaitForCacheSync` 会永久等待。

正确顺序：

```go
podInformer := factory.Core().V1().Pods()
sharedPodInformer := podInformer.Informer()
factory.Start(ctx.Done())
cache.WaitForCacheSync(ctx.Done(), sharedPodInformer.HasSynced)
```

## 考虑过但没有采用的方案

- 每秒 List API Server：实现直观，但 QPS 随模块和副本增长。
- handler 直接 Create/Delete：事件密集时难保证幂等，错误重试也分散。
- 多 worker：池只有一个固定 key，多 worker没有收益，反而增加理解成本。
- 内存计数器维护池大小：进程重启或事件丢失后会和真实集群漂移。
- CRD/Operator：生产表达力更强，但超出最小面试 Demo。

## 常见错误

- informer 未 sync 就开始读，误把空缓存当成集群没有对象；
- Update handler 做耗时网络请求，阻塞后续事件；
- 修改 informer 返回的对象，污染共享缓存；
- Reconcile 依赖“上一次内存状态”，无法从当前集群状态恢复；
- 失败 AddRateLimited 后忘记成功 Forget；
- Manager 和 Pool 各建一个 informer，产生重复 List/Watch。

## 面试高频问答

**问：Informer 是否保证强一致？**

答：不保证，它是最终一致的本地缓存；对展示和候选筛选足够，但并发写正确性要靠 API Server resourceVersion 或原子 Patch。

**问：为什么控制器强调幂等？**

答：事件可能重复、丢失后由 resync 补发，失败还会重试。Reconcile 必须从当前状态计算结果，执行多次和一次效果一致。

**问：固定 key 有什么好处？**

答：本项目只管理一个池，不需要为每个 Pod 单独 reconcile。固定 key 把所有 Pod 事件合并为“池状态可能变了”，天然去重和防抖。

## 验证命令

```bash
go test ./internal/sandbox -run TestWaitReady -count=10
./hack/verify-pool.sh
```

## 一分钟项目讲法

Manager 和预热池共享一个 filtered informer，只 watch sandboxd 管理的 Pod。事件 handler 不做业务，只把固定 key 放进 typed workqueue；单 worker 再从 lister 当前快照做幂等 Reconcile。这样事件风暴被 queue 去重，读取不打 API Server，进程重启后也能从真实状态恢复。缓存是最终一致的，所以它只用于筛选候选，Claim 的并发正确性仍由 API Server JSON Patch CAS 保证。
