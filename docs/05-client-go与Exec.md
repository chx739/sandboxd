# 模块 05：client-go、Informer 与 Exec

## 这个模块解决什么问题

前几个模块定义了安全 Pod 和集群边界，本模块把它们变成可调用的生命周期服务：创建沙箱、等待真正 Ready、列出、删除，以及通过 Kubernetes API Server 在容器内执行命令。

## 项目里的最小实现

- `Manager.Create`：生成随机 ID、调用唯一的 `BuildPod`、创建并等待 Ready；
- `Manager.WaitReady`：只读 informer/lister 本地缓存；
- `Manager.Delete`：foreground propagation，NotFound 视为幂等成功；
- `Manager.Exec`：WebSocket 优先、SPDY fallback，返回真实 exit code；
- HTTP API：create/list/delete/exec、health/ready；
- 安全默认值：只监听 `127.0.0.1:8080`，`SANDBOXD_TOKEN` 为空拒绝启动，请求体 16 KiB、stdout/stderr 各 64 KiB。

## 代码阅读顺序

1. `cmd/sandboxd/main.go`：client、informer、Manager、HTTP Server 的装配顺序；
2. `internal/sandbox/manager.go`：创建、缓存等待、删除和失败清理；
3. `internal/sandbox/exec.go`：pods/exec URL 与两种流协议；
4. `internal/api/server.go`：路由和 Bearer Token；
5. `internal/api/handler.go`：超时、输入/输出边界和状态码；
6. `hack/verify-manager.sh`：真实闭环验收。

## client-go 的三类常见对象

### ClientSet

ClientSet 是对 Kubernetes 分组版本 API 的类型化客户端。创建 Pod 走 `CoreV1().Pods(ns).Create`，删除走 Delete，exec 则从 RESTClient 构造 subresource 请求。

### Informer

Informer 先 List 获得全量对象，再 Watch 增量事件，并把对象放进本地线程安全缓存。它减少重复请求 API Server，并为后续控制器提供事件来源。

### Lister

Lister 是 informer 缓存的只读查询接口，不访问 API Server。它速度快，但具有最终一致性；因此创建请求返回后，Pod 可能短时间还没出现在 lister 中，`WaitReady` 会等待下一个 ticker。

## 为什么 Running 不等于 Ready

`status.phase=Running` 只说明至少一个容器在运行，readiness probe 可能还未通过。Manager 只接受 `PodReady=True`，避免 API 太早返回后第一次 exec 失败。单测专门构造 Running 但未 Ready 的 Pod锁住这个差异。

## Exec 的真实链路

```text
HTTP API
  -> Manager.Exec
  -> POST /api/v1/namespaces/{ns}/pods/{pod}/exec
  -> API Server 协议升级
  -> WebSocket（优先）或 SPDY（兼容回退）
  -> kubelet / container runtime
  -> gVisor 容器进程
```

pods/exec 不是普通请求/响应 HTTP，而是多路复用 stdin、stdout、stderr 和错误通道的流协议。

## 为什么不能对所有 Exec 错误 fallback

只有 WebSocket 升级失败或 HTTPS proxy 错误才回退 SPDY。如果容器命令 `exit 7` 也回退，命令会被执行第二次，非幂等操作可能产生重复副作用。

本项目只对 `IsUpgradeFailure`、`IsHTTPSProxyError` fallback；`utilexec.ExitError` 直接提取 exit status 返回。

## 超时为什么删除整个沙箱

远程流断开不一定能证明容器内所有子进程都结束，复用可能残留状态。ctx 超时或取消后，Manager 使用新的 10 秒 cleanup context 删除 Pod。不能继续用原 ctx，因为它已经 Done，Delete 会立即失败。

## HTTP 层的最小安全边界

- 默认 loopback，不暴露到局域网；
- Token 只从环境变量读取，不放 CLI 参数；
- 常量时间比较 Authorization；
- ID 只允许 `[a-z0-9-]{1,63}`；
- JSON 拒绝未知字段，请求体最多 16 KiB；
- stdout/stderr 各缓存最多 64 KiB，防止命令输出撑爆服务内存；
- 服务端对 create/exec 强制超时。

## 真实踩坑：Informer factory 的启动顺序

**现象**：进程存在、API Server 正常，但 8080 一直不监听，停进程时才打印“等待 Pod informer 缓存同步失败”。

**根因**：`factory.Core().V1().Pods()` 只取得 typed wrapper；第一次调用 `.Informer()` 才把 shared informer 注册到 factory。原代码先 `factory.Start()`，后在 `WaitForCacheSync` 参数中第一次 `.Informer()`，所以 Start 当时没有任何 informer 可启动。

**修复**：在 `factory.Start()` 前保存 `sharedPodInformer := podInformer.Informer()`，再等待 `sharedPodInformer.HasSynced`。修改后服务立即监听。

**面试点**：Informer 不只是“会用 List/Watch”，还要理解 lazy initialization、factory 生命周期和 cache sync barrier。

## 其他常见错误

- WaitReady 每 100ms 调一次 ClientSet Get，把轮询压力直接打到 API Server；
- 创建失败后继续用已经超时的 ctx 清理；
- SPDY fallback 对所有错误生效，重复执行命令；
- 把 stdout/stderr 合并，调用方无法区分业务输出和错误；
- HTTP 直接绑定 `0.0.0.0` 且无鉴权；
- list 接口直接查 API Server，没有复用 informer 缓存；
- direct sandbox 先标 idle 再改 busy，可能被预热池并发认领。

## 面试高频问答

**问：Informer 为什么比轮询好？**

答：它用一次 List + 长连接 Watch 维护本地缓存，多个读取复用同一份数据，降低 API Server QPS；同时通过 resourceVersion 保持增量事件顺序。代价是最终一致性，所以启动前要 WaitForCacheSync，读取时要接受短暂延迟。

**问：为什么还有 100ms ticker？**

答：ticker 只读本地 lister，不发网络请求。最小实现不额外为每次 Create 建事件通道；后续 Pool 则会使用 informer handler + workqueue 做事件驱动 reconcile。

**问：SPDY 已弃用为什么还保留？**

答：新集群优先 WebSocket，但某些代理或旧服务端不支持。只对传输升级类错误回退可以兼容环境，同时避免业务命令被重放。

## 实测结果

```text
HTTP auth: unauthorized -> 401
Create + informer Ready: -> 201
Exec success: exitCode=0，stdout/stderr 已分离
Exec failure: exitCode=7，未错误 fallback
Exec timeout: -> 504，Pod 已删除
Delete: -> 204，managed Pod=0
```

## 验证命令

```bash
go test ./...
go vet ./...
go build ./...
./hack/verify-manager.sh
```

## 一分钟项目讲法

我用 client-go ClientSet 创建和删除 Pod，用 filtered informer 只 watch sandboxd 自己的 Pod，再通过 lister 本地缓存等待 `PodReady=True`，避免高并发轮询 API Server。命令执行走 pods/exec 协议升级，优先 WebSocket，仅在升级或代理错误时回退 SPDY，命令本身 exit 7 不会被重试。超时后原 context 已失效，所以用独立 cleanup context 删除整个沙箱，防止残留进程进入复用池。HTTP 层只监听 loopback、强制 Bearer Token，并限制请求体、ID 和输出大小。
