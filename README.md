# sandboxd

`sandboxd` 是一个面向学习和秋招面试的 Kubernetes AI Agent 执行沙箱 Demo。

项目重点不是生产化，而是通过一条能运行、能验证的最小链路理解并展示：

- gVisor 与普通容器的隔离边界；
- Pod `securityContext`、ServiceAccount、RBAC 与 NetworkPolicy；
- client-go remotecommand、informer、lister 和 workqueue；
- 预热池与 JSON Patch CAS 并发认领；
- Prometheus 低基数指标与可观测性；
- server-side dry-run 与受控写操作。

## 当前状态

已经实测完成：

- 环境：Go 1.26.5、kind 0.31.0、Kubernetes 1.35.0、gVisor `release-20260810.0`、Calico 3.32.0；
- 安全：Pod restricted 基线、PSA、只读 RBAC、secrets/写入/exec 拒绝、DNS/API 之外默认拒绝网络；
- 生命周期：Create/List/Delete、informer/lister 等待 Ready、WebSocket/SPDY Exec、超时销毁、本地鉴权 API；
- 并发：typed workqueue 单 worker、目标为 2 的预热池、JSON Patch CAS、direct fallback、Release 后删除补新；
- 可观测：acquire、pool size、CAS conflict、Exec duration/timeout、runtime 和审批拒绝指标。

关键实测结果：

```text
[   0.000000] Starting gVisor...
RBAC: get pods --all-namespaces -> yes
RBAC: create pods --namespace sandboxd-demo -> no
NetworkPolicy + token + RBAC: 集群内读取 Pod -> HTTP 200
HTTP auth: unauthorized -> 401
Exec failure: exitCode=7，未错误 fallback
Exec timeout: -> 504，Pod 已删除
Pool target: 2 Ready idle
Concurrent Claim: 5 requests, 5 unique IDs
Claim source: pool=2, direct=3
Release + reconcile: idle restored to 2
Metrics: runtime=gvisor, idle=2, busy=0, claim_conflicts=6
Metrics: direct acquire=2, exec=3, timeout=1
```

证据记录位于 [docs/evidence](docs/evidence)，当前开发位置见 [持续进度](docs/PROGRESS.md)。

## 快速验证

以下脚本默认只写用户目录和本仓库缓存，不需要 sudo：

```bash
export PATH="${HOME}/.local/bin:${PATH}"
./hack/install-tools.sh
./hack/create-cluster.sh
./hack/install-calico.sh
./hack/verify-gvisor.sh
./hack/verify-security.sh
./hack/verify-manager.sh
./hack/verify-pool.sh
```

脚本在重操作前检查内存、swap、磁盘和容器。验证脚本使用低副本、小并发，并在退出时精确清理。

## 编译与测试

```bash
make build
make test
```

测试保持少而有价值：优先锁住安全基线和并发不变量，不追求覆盖率数字。

## 文档入口

- [持续开发目标与安全边界](GOAL.md)
- [最小实现计划](docs/00-实现计划.md)
- [学习文档索引](docs/README.md)
- [开发踩坑与排障](docs/11-开发踩坑与排障.md)

## 项目边界

这是单机、单进程、单租户的教学与面试 Demo，不具备生产环境要求的多租户隔离、高可用、持久化审计、凭证轮转和完整限流能力。gVisor 是纵深防御的一层，不是“绝对安全”的承诺。
