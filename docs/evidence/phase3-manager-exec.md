# 阶段 3 验证记录：Manager + Informer + Exec + HTTP API

验证日期：2026-08-18

## 验证范围

- 服务只监听 `127.0.0.1:8080`；
- 无 Authorization 请求拒绝；
- 创建真实 gVisor Pod，并通过 informer/lister 等待 Ready；
- WebSocket Exec 分离 stdout/stderr；
- 非零 exit status 原样返回且不 fallback 重放；
- 超时返回 504 并删除 Pod；
- DELETE 返回 204 并删除 Pod；
- 退出后 managed Pod 为 0。

## 实测输出

```text
healthz -> 200 ok
unauthorized_http=401
create_http=201
state=busy source=direct

exec success:
exitCode=0
stdout="stdout-ok\n"
stderr="stderr-ok\n"

exec non-zero:
exitCode=7
stdout="before-exit\n"
error="command terminated with exit code 7"

exec timeout:
timeout_http=504
error="context deadline exceeded"
Pod delete condition met

explicit delete:
delete_http=204
Pod delete condition met
managed Pod=0
```

## 真实问题与修复

第一次启动时 informer factory 在 8080 监听前永久等待。原因是 shared informer 在 `factory.Start()` 之后才通过 `.Informer()` 懒加载注册。把实例化移动到 Start 前后，缓存同步和监听立即恢复。详细分析见 `docs/05-client-go与Exec.md`。

## 资源与清理

- 每次只存在一个业务沙箱 Pod；
- Pod limit 为 500m CPU、256 MiB 内存；
- 测试完成后 WSL 可用内存约 6.3 GiB，swap 为 0；
- sandboxd 进程停止、8080 不再监听、managed Pod 数量为 0；
- 未使用 sudo。

## 自动复现

```bash
./hack/verify-manager.sh
```
