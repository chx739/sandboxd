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

阶段 0 的 WSL2 + kind + gVisor + Calico 环境闭环已经实测通过：

- Go 1.26.5、kind 0.31.0、Kubernetes 1.35.0；
- gVisor `release-20260810.0`，默认 systrap；
- kind 单 control-plane 节点，containerd 2.2.0；
- Calico 3.32.0；
- 临时 Pod 内实测出现 `[0.000000] Starting gVisor...`。

阶段 1 的 PodSpec 安全基线也已完成：安全字段集中在 `BuildPod`，测试锁定非 root、禁止提权、只读根文件系统、drop ALL、seccomp、资源限制、有界 emptyDir 和受控 projected token。`go test`、`go vet`、`go build` 均已通过。

详细环境输出见 [阶段 0 验证记录](docs/evidence/phase0-gvisor.md)。当前开发位置见 [持续进度](docs/PROGRESS.md)。

## 环境快速复现

以下脚本默认只写用户目录和本仓库缓存，不需要 sudo：

```bash
export PATH="${HOME}/.local/bin:${PATH}"
./hack/install-tools.sh
./hack/create-cluster.sh
./hack/install-calico.sh
./hack/verify-gvisor.sh
```

每次重操作前，脚本都会检查可用内存、swap、磁盘和现有容器。`verify-gvisor.sh` 只创建一个限制为 100m CPU、32 MiB 内存的临时 Pod，验证后自动删除。

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
- [gVisor 与容器隔离](docs/01-gVisor与容器隔离.md)
- [Pod 安全基线](docs/02-Pod安全基线.md)

## 项目边界

这是单机、单进程、单租户的教学与面试 Demo，不具备生产环境要求的多租户隔离、高可用、持久化审计、凭证轮转和完整限流能力。gVisor 是纵深防御的一层，不是“绝对安全”的承诺。
