# 阶段 0 验证记录：WSL2 + kind + gVisor + Calico

验证日期：2026-08-18

## 验证环境

```text
WSL: Ubuntu 24.04 / WSL2
CPU: 16
内存: 7.7 GiB
swap: 2.0 GiB
Go: go1.26.5 linux/amd64
kind: v0.31.0
Kubernetes: v1.35.0
kind node image: kindest/node:v1.35.0@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f
containerd: 2.2.0
gVisor: release-20260810.0
Calico: 3.32.0
```

gVisor dated release 包的 SHA-512：

```text
3de91138cda15682c11807387f6ecad9e7c8932262018a2813277e1b4efa03efe33b0a948e148c6b1ccfe7345bfab5d5e0d072519505465751273898bae19c62
```

## 配置侧证据

节点内存在完整的新版 gVisor 文件：

```text
/usr/local/bin/runsc
/usr/local/bin/containerd-shim-runsc-v1
/usr/local/bin/gvisor-bin/gvisor_sentry
```

`containerd config dump` 将 kind 配置转换为 containerd 2.x 的 version 3 配置：

```toml
[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runsc]
  runtime_type = 'io.containerd.runsc.v1'
```

Calico 和节点状态：

```text
sandboxd-control-plane   Ready   control-plane   v1.35.0   containerd://2.2.0
calico-node              1/1     Running
calico-kube-controllers  1/1     Running
```

## 运行侧证据

`hack/verify-gvisor.sh` 创建带 `runtimeClassName: gvisor` 的临时 Pod，等待 Ready 后执行 Pod 内 `dmesg`。实测输出：

```text
RuntimeClass: gvisor
[   0.000000] Starting gVisor...
```

脚本成功后删除精确的 `sandboxd-demo/gvisor-smoke` Pod，只保留 RuntimeClass、namespace 和集群供后续开发使用。

这组证据同时证明：

1. Kubernetes PodSpec 选择了 `gvisor` RuntimeClass；
2. RuntimeClass 对应的 containerd handler 已在节点注册；
3. 容器内部看到的是 gVisor Sentry 提供的内核视图，不是普通 runc 容器。

它不证明整个项目已经达到生产级安全，也不替代后续 Pod 安全上下文、RBAC 和 NetworkPolicy 验证。

## 资源安全结果

Calico Ready 且 smoke test 完成后：

```text
WSL 可用内存: 约 5.6 GiB
swap: 基本未使用
sandboxd-control-plane 内存: 约 1.1 GiB
Docker 容器数量: 1
```

整个阶段只创建一个 kind 节点；smoke Pod 的限制为 100m CPU、32 MiB 内存并在验证后删除。工具安装和集群创建均未使用 sudo。

## 复现命令

```bash
export PATH="${HOME}/.local/bin:${PATH}"
./hack/install-tools.sh
./hack/create-cluster.sh
./hack/install-calico.sh
./hack/verify-gvisor.sh
```

上游依据：

- [gVisor 官方安装文档](https://gvisor.dev/docs/user_guide/install/)
- [gVisor containerd 文档](https://gvisor.dev/docs/user_guide/containerd/quick_start/)
- [kind 配置文档](https://kind.sigs.k8s.io/docs/user/configuration/)
- [Kubernetes SIG agent-sandbox 的 gVisor-on-kind 示例](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/examples/quickstart/gvisor.md)
