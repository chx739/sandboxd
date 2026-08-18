# sandboxd

`sandboxd` 是一个面向学习和秋招面试的 Kubernetes AI Agent 执行沙箱 Demo。

项目重点不是生产化，而是通过一条能运行、能验证的最小链路理解并展示：

- gVisor 与普通容器的隔离边界；
- Pod `securityContext` 与 Pod Security Admission；
- ServiceAccount、projected token 与只读 RBAC；
- NetworkPolicy 与 CNI 的职责；
- client-go remotecommand、informer、lister 和 workqueue；
- 预热池与 JSON Patch CAS 并发认领；
- server-side dry-run 与受控写操作。

## 当前状态

已经实测完成以下模块：

- 环境：Go 1.26.5、kind 0.31.0、Kubernetes 1.35.0、gVisor `release-20260810.0`、Calico 3.32.0；
- Pod 基线：非 root、禁止提权、只读根、drop ALL、seccomp、资源限制、有界 emptyDir、短期 projected token；
- 权限与网络：PSA restricted、只读 RBAC、secrets/写入/exec 拒绝、默认拒绝网络、只允许 DNS 和 API Server；
- 生命周期与执行：client-go Create/List/Delete、informer/lister 等待 Ready、WebSocket/SPDY Exec、超时销毁、本地鉴权 HTTP API。

关键实测结果：

```text
[   0.000000] Starting gVisor...
RBAC: get pods --all-namespaces -> yes
RBAC: create pods --namespace sandboxd-demo -> no
NetworkPolicy + token + RBAC: 集群内读取 Pod -> HTTP 200
NetworkPolicy: https://example.com -> 已按预期拒绝
HTTP auth: unauthorized -> 401
Create + informer Ready: -> 201
Exec failure: exitCode=7，未错误 fallback
Exec timeout: -> 504，Pod 已删除
Delete: -> 204，managed Pod=0
```

证据记录：

- [gVisor 环境](docs/evidence/phase0-gvisor.md)
- [PSA、RBAC 与 NetworkPolicy](docs/evidence/phase2-security.md)
- [Manager、Informer、Exec 与 API](docs/evidence/phase3-manager-exec.md)

当前开发位置见 [持续进度](docs/PROGRESS.md)。

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
```

脚本在重操作前检查内存、swap、磁盘和容器。验证脚本串行创建受资源限制的临时 Pod，并在退出时精确删除。

## 编译与测试

```bash
make build
make test
```

测试保持少而有价值：优先锁住安全基线和后续并发不变量，不追求覆盖率数字。

## 文档入口

- [持续开发目标与安全边界](GOAL.md)
- [最小实现计划](docs/00-实现计划.md)
- [学习文档索引](docs/README.md)
- [开发踩坑与排障](docs/11-开发踩坑与排障.md)

## 项目边界

这是单机、单进程、单租户的教学与面试 Demo，不具备生产环境要求的多租户隔离、高可用、持久化审计、凭证轮转和完整限流能力。gVisor 是纵深防御的一层，不是“绝对安全”的承诺。
