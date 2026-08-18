# 模块 02：Pod 安全基线

## 这个模块解决什么问题

gVisor 加强的是容器与宿主内核之间的边界，但它不会自动限制容器身份、磁盘写入、资源消耗或 Kubernetes API 权限。`BuildPod` 把这些容易遗漏的约束集中写进一个 PodSpec，避免 Manager、预热池和直接创建路径各自拼装出不同安全级别的 Pod。

本模块的核心原则是：

> 创建沙箱的路径可以有多个，但安全 Pod 模板只能有一个。

## 项目里的最小实现

实现位于 `internal/sandbox/spec.go`，入口只有：

```go
func BuildPod(cfg Config, id string) *corev1.Pod
```

它固定设置：

- `runtimeClassName: gvisor`，同时允许空值用于无 gVisor 的纯逻辑开发；
- `runAsNonRoot`，UID/GID/FSGroup 为 65532；
- `allowPrivilegeEscalation: false`；
- `readOnlyRootFilesystem: true`；
- drop `ALL` Linux capabilities；
- `seccompProfile: RuntimeDefault`；
- CPU、内存 request 和 limit；
- 最长存活 1 小时，终止宽限 5 秒；
- 禁止 Service link 环境变量注入；
- 禁止自动挂载 ServiceAccount token；
- 手动 projected 短期 token、集群 CA 和 namespace；
- `/workspace` 与 `/tmp` 两个有容量上限的可写目录。

## 代码阅读顺序

1. `internal/sandbox/types.go`：状态、label key 和最小 Config。
2. `internal/sandbox/spec.go`：完整 PodSpec 和每项安全理由。
3. `internal/sandbox/spec_test.go`：哪些约束被测试锁死。
4. `docs/01-gVisor与容器隔离.md`：RuntimeClass 最终如何落到 runsc。

`BuildPod` 不访问 Kubernetes、不读环境变量、也不执行 I/O，因此可以快速、确定地测试。

## 必须掌握的基础知识

### namespace 与 cgroup 分别做什么

Linux namespace 隔离进程看到的资源视图，例如 PID、mount、network 和 hostname。cgroup 负责资源统计与限制，例如 CPU 和内存。前者回答“能看到谁”，后者回答“最多能用多少”。它们都不等于独立内核。

### capability 为什么要 drop ALL

传统 root 权限被 Linux 拆成多项 capability，例如修改网络配置、绕过文件权限检查。容器即使不是 privileged，也可能默认获得一组 capability。本项目先全部删除；确有需求时应逐项添加，而不是保留宽泛默认权限。

### allowPrivilegeEscalation 与 no_new_privs

Kubernetes 的 `allowPrivilegeEscalation: false` 会要求容器进程不能通过 setuid、setgid 或文件 capability 获得更多权限，Linux 侧通常通过 `no_new_privs` 实现。它和 `runAsNonRoot` 解决的问题不同，因此两者都要设置。

### seccomp 做什么

seccomp 根据系统调用过滤规则限制进程可调用的内核接口。`RuntimeDefault` 使用容器运行时的默认配置，比 `Unconfined` 少暴露一批危险或少用 syscall。gVisor 已有用户态系统调用边界，但 seccomp 仍是外层纵深防御。

### request 与 limit 的区别

- request 主要参与调度，表示 Pod 需要的资源基线。
- limit 是运行时上限；CPU 超限通常被 throttling，内存超限可能触发 OOM kill。

只设 request 不能阻止沙箱耗尽节点，只设 limit 又可能让调度器低估资源需求。

## 为什么采用当前方案

### 为什么根文件系统只读还要挂载两个目录

只读根文件系统可以减少篡改系统文件和持久化恶意内容的机会，但很多命令需要临时文件或 HOME。项目提供：

- `/workspace`：普通磁盘 `emptyDir`，64 MiB；
- `/tmp`：内存型 `emptyDir`，32 MiB；
- `HOME=/workspace`。

这样既保留最小可用性，又明确限制可写区域和容量。

### 为什么 emptyDir 必须有 sizeLimit

没有 `sizeLimit` 的磁盘型 `emptyDir` 可能写满节点磁盘；内存型 `emptyDir` 还会计入容器内存使用。对会执行不可信代码的沙箱来说，这是直接的资源耗尽入口，因此两种卷都设上限。

### 为什么关闭自动 token 后再手动 projected

`automountServiceAccountToken: false` 先关闭隐式行为，再显式投影：

- 1 小时有效期且由 kubelet 轮转的 token；
- `kube-root-ca.crt`；
- 当前 namespace。

挂载路径保持 Kubernetes 默认路径，因此容器内的 kubectl 和 client-go 仍可自动使用 in-cluster config。真正能做哪些 API 操作由下一模块 RBAC 决定。

### 为什么 RuntimeClass 可以为空

为空时不写 `runtimeClassName`，便于没有 gVisor 的环境运行纯逻辑测试。这只是开发降级，不代表可以把 runc 结果当成 gVisor 证据；正式 Demo 固定使用 `gvisor`。

## 考虑过但没有采用的方案

- privileged 容器：权限过大，与不可信代码目标冲突。
- hostPath 工作目录：直接暴露节点文件系统，不适合沙箱。
- 无限 writable layer：简单但容易造成磁盘 DoS，也更难解释写入边界。
- 自动 ServiceAccount token：配置少一行，但权限来源不显式，难审计有效期和投影内容。
- 每个调用方单独构造 Pod：短期方便，长期必然出现安全字段漂移。

## 常见错误和本项目踩坑

- 只设置 `runAsNonRoot`，镜像却声明 root 用户且没有指定 UID，导致准入或启动失败。
- 设置只读根文件系统，却忘记 `/tmp` 和 HOME，工具运行时才报只读错误。
- drop capability 但遗漏 `allowPrivilegeEscalation: false`。
- 只设 CPU limit，不设内存 limit，仍可能拖垮节点。
- 关闭自动 token 后没有投影 CA 和 namespace，导致 in-cluster 客户端无法工作。
- 认为 `RuntimeClassName` 字段存在就证明 gVisor；真实证据仍需 Pod 内 `dmesg`。

## 面试高频问题与回答思路

**问：runAsNonRoot 和 runAsUser 有什么区别？**

答：`runAsNonRoot` 是约束，要求最终用户不能是 UID 0；`runAsUser` 是明确指定 UID。两者同时设置可以减少镜像元数据不明确带来的歧义。

**问：容器已经有 gVisor，为什么还需要 seccomp 和 capability？**

答：这是纵深防御。gVisor 降低直接攻击宿主内核的面积；capability 限制进程权限，seccomp 限制可用 syscall。任一层配置出错时，其他层仍能缩小风险。

**问：为什么不完全删除 ServiceAccount token？**

答：项目要演示 Agent 的“只读 Kubernetes 能力”，因此需要受 RBAC 约束的短期凭证。如果业务不需要访问 API，最安全的选择确实是不挂 token。

**问：如何防止后续开发把安全字段改没？**

答：所有 Pod 都从 `BuildPod` 创建，并用单元测试断言非 root、禁止提权、只读根、drop ALL、seccomp、资源 limit、deadline 和 emptyDir sizeLimit。安全基线变化会直接让测试失败。

## 自己动手验证

```bash
go test ./internal/sandbox -run TestBuildPodSecurityBaseline -v
go test ./...
go build ./...
```

当前实测结果：

```text
ok github.com/chx739/sandboxd/internal/sandbox
```

后续 namespace 加入 Pod Security Admission restricted 标签后，还会用 API Server dry-run 验证生成的 Pod 能通过准入。

## 一分钟项目讲法

我把沙箱安全字段集中在一个纯函数 `BuildPod`，避免预热池和直接创建路径出现配置漂移。Pod 使用 gVisor，同时显式设置非 root、drop ALL capability、禁止权限提升、RuntimeDefault seccomp 和只读根文件系统。为了让工具仍可运行，我只开放有容量上限的 `/workspace` 和 tmpfs `/tmp`。Kubernetes token 不用默认自动挂载，而是手动投影一小时短期 token、CA 和 namespace，权限再由只读 RBAC 控制。最后我用少量单元测试锁死这些关键字段，重点不是覆盖率，而是防止安全基线被后续修改静默降级。
