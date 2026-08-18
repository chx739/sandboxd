# 模块 01：gVisor 与容器隔离

## 这个模块解决什么问题

普通容器和宿主机共享 Linux 内核。namespace、cgroup、capability、seccomp 可以缩小攻击面，但容器中的不可信 AI Agent 仍会把大量系统调用直接交给宿主内核处理。gVisor 在应用和宿主内核之间加入用户态内核 Sentry，拦截并实现大部分 Linux 系统调用，从而减少不可信工作负载直接接触宿主内核的机会。

本项目使用 gVisor 不是为了宣称“绝对安全”，而是演示纵深防御：

```text
不可信命令
  -> Pod 安全上下文
  -> gVisor Sentry
  -> Kubernetes RBAC
  -> NetworkPolicy
  -> 宿主 Linux 内核
```

## 本项目的最小实现

- WSL2 中运行一个单节点 kind 集群。
- 把完整 gVisor 发布包只读挂载到 kind 节点。
- 为节点 containerd 注册 `runsc` runtime handler。
- 创建 `RuntimeClass/gvisor`。
- 沙箱 Pod 设置 `runtimeClassName: gvisor`。
- 在 Pod 内执行 `dmesg`，以出现 `Starting gVisor` 作为真实运行证据。

工具版本锁定为：

| 工具 | 版本 |
|---|---|
| Go | 1.26.5 |
| kind | 0.31.0 |
| Kubernetes 节点 | 1.35.0 |
| gVisor | release-20260810.0 |
| gVisor platform | systrap |

## 为什么 WSL2 使用 systrap

WSL2 本身运行在虚拟机中。gVisor 的 KVM platform 需要嵌套虚拟化能力，环境差异更大；systrap 不依赖 `/dev/kvm`，更适合“已经在虚拟机里”的开发环境。它不是所有环境下性能最优的选择，但更符合本项目稳定、简单、面试可复现的目标。

## 为什么必须挂载整个 gVisor 包

2026 年 7 月后的 gVisor 发布包不再只有 `runsc` 和 containerd shim，还包含 `gvisor-bin/` 下的 Sentry 等 sidecar。`runsc` 会在自身相邻目录寻找这些程序。只复制两个旧式二进制文件可能在版本检查时正常，却在真正创建容器时失败。

因此 kind 节点应看到同一目录中的：

```text
runsc
containerd-shim-runsc-v1
gvisor-bin/gvisor_sentry
gvisor-bin/checkpointgofer
gvisor-bin/runsc-metric-server
```

## 代码和配置阅读顺序

1. `hack/install-tools.sh`：固定版本、下载与校验。
2. `hack/check-resources.sh`：WSL 重操作前的资源保护。
3. `deploy/kind/config.yaml`：挂载 gVisor 并注册 containerd handler。
4. `deploy/runtimeclass.yaml`：把 Kubernetes RuntimeClass 映射到 `runsc`。
5. `hack/verify-gvisor.sh`：创建最小 Pod 并保存验证证据。

以上文件均已完成，并已在当前 WSL2 环境通过真实集群验证。

## 面试八股

### 容器与虚拟机的隔离差异

- 容器主要共享宿主内核，通过 namespace 隔离视图、cgroup 管资源、capability 拆分 root 权限。
- 虚拟机通常拥有独立 guest kernel，隔离边界更厚，但启动和资源开销通常更高。
- gVisor 位于两者之间：保持 OCI 容器接口，同时用用户态内核承接系统调用；兼容性和性能会有代价。

### OCI runtime、containerd shim、RuntimeClass 分别是什么

- OCI runtime 负责按照 OCI bundle 创建容器进程，常见的是 `runc`，本项目使用 `runsc`。
- containerd shim 管理容器生命周期，并让 containerd 不必成为所有容器进程的直接父进程。
- RuntimeClass 是 Kubernetes 选择节点运行时 handler 的 API；它本身不安装运行时。

### gVisor 不能替代什么

gVisor 不能替代最小权限、RBAC、NetworkPolicy、镜像治理和资源限制。它主要加强系统调用边界；错误开放的 Kubernetes 权限或网络出口依然可能被滥用。

## 常见坑

- 只创建 RuntimeClass，节点 containerd 没有对应 handler。
- 只挂载 `runsc`，遗漏新版 `gvisor-bin/`。
- gVisor 文件放在 Windows 挂载盘，权限或文件语义不符合 Linux 运行要求。
- 在 WSL2 默认选择 KVM，随后被嵌套虚拟化问题阻塞。
- 只看 Pod 为 Running 就声称用了 gVisor，没有检查 `dmesg` 或节点运行时状态。

## 面试问答

**问：为什么不直接用普通 Docker 容器？**

答：AI Agent 会执行模型生成的命令，输入不完全可信。普通容器仍直接共享宿主内核，所以我在 namespace、capability、seccomp 之外增加 gVisor 用户态内核，让攻击者必须再跨过一层系统调用隔离边界。

**问：用了 gVisor 是否就安全了？**

答：不是。gVisor 只解决隔离链路的一部分。本项目仍使用非 root、只读根文件系统、drop ALL capability、RBAC 和默认拒绝网络策略，而且明确把它定位为 Demo 而非生产安全承诺。

**问：怎么证明 Pod 真的由 gVisor 运行？**

答：RuntimeClass 和 PodSpec 只能证明“请求了 runsc”。我还会在 Pod 内执行 `dmesg`，检查 `Starting gVisor`，并查看节点 containerd runtime 配置，形成配置与运行时两侧证据。

## 最小验证命令

```bash
./hack/check-resources.sh
./hack/install-tools.sh
runsc --version
kind version
```

真实集群跑通后，验证入口会统一为：

```bash
./hack/verify-gvisor.sh
```

## 一分钟项目讲法

我的 sandboxd 面向会执行不可信命令的 AI Agent。第一层不是直接依赖普通容器，而是在 kind 集群中注册 gVisor 的 runsc runtime，通过 RuntimeClass 让沙箱 Pod 使用用户态内核。WSL2 是虚拟化环境，所以我选择不依赖嵌套虚拟化的 systrap。为了避免“配置写了但实际没用”，演示会在 Pod 内检查 `Starting gVisor`。gVisor 之外，我还叠加 Pod 安全上下文、RBAC 和 NetworkPolicy；这体现的是纵深防御，而不是把某一个组件当成万能沙箱。
