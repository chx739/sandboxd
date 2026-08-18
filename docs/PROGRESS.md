# sandboxd 持续开发进度

> 本文件是跨会话进度快照。每完成一个可验证步骤就更新它，避免上下文压缩后重复工作或偏移目标。目标和边界以 `../GOAL.md` 为准。

## 当前阶段

阶段 1：建立 Go 工程骨架和 PodSpec Builder，落实沙箱 Pod 的最小安全基线。

## 已完成

- 创建公开 GitHub 仓库：`https://github.com/chx739/sandboxd`，当前开发分支为 `agent/environment-gvisor`。
- 固化 `GOAL.md`、`AGENTS.md` 和本进度文件，建立跨上下文续作协议。
- 确认环境为 WSL2 Ubuntu 24.04、systemd、cgroup v2、Docker Engine 和 containerd 可用。
- 确认宿主资源适合低资源 Demo：16 CPU、约 7.7 GiB 内存、2 GiB swap；采用单节点和小并发策略。
- 安装并验证 Go 1.26.5、kind 0.31.0、gVisor release-20260810.0；工具位于用户目录，不修改系统级运行时。
- 完成可重复的 `hack/install-tools.sh` 和只读资源门 `hack/check-resources.sh`。
- 创建固定 Kubernetes 1.35.0 的单节点 kind 集群，节点 containerd 2.2.0 已注册 `io.containerd.runsc.v1`。
- 完整挂载新版 gVisor 的 `runsc`、containerd shim 和 `gvisor-bin/`。
- 安装 Calico 3.32.0，节点、calico-node 和 calico-kube-controllers 均 Ready。
- 真实运行 gVisor smoke Pod：`RuntimeClass=gvisor`，Pod 内 `dmesg` 出现 `[0.000000] Starting gVisor...`。
- 完成 `docs/01-gVisor与容器隔离.md` 和 `docs/evidence/phase0-gvisor.md`。

## 正在进行

- 初始化 Go module 和最小目录结构。
- 实现只负责构造安全 Pod 的 PodSpec Builder。
- 为安全基线保留少量高价值单元测试。

## 紧接着做

1. 创建 `go.mod`、`cmd/sandboxd` 和 `internal/sandbox`。
2. 实现 gVisor RuntimeClass、非 root、drop ALL、seccomp、禁止提权、只读根文件系统和资源限制。
3. 为 `/tmp`、`/workspace` 使用带 `sizeLimit` 的 `emptyDir`。
4. 默认禁止自动挂载 ServiceAccount token；随后加入受控 projected token 版本。
5. 完成 `docs/02-Pod安全基线.md`，编译并运行最小测试。
6. 进入 ServiceAccount/RBAC 和 NetworkPolicy 模块。

## 资源与安全约束

- kind：单 control-plane 节点；当前节点容器稳定后约占 1.1 GiB。
- 预热池默认：2。
- 并发验证默认：5；资源确认充足后上限 10。
- 可用内存接近 2 GiB、持续使用 swap 或 WSL/Docker 异常时停止测试。
- sudo 只用于经过确认、无法由普通用户完成的最小系统操作；密码不进入命令、脚本、日志或 Git。
- 只清理名称和来源都能确认属于 sandboxd 的容器、集群、网络和临时文件。

## 尚未开始

- ServiceAccount/RBAC 和 NetworkPolicy 策略。
- Manager、Exec、HTTP API。
- Informer/Workqueue、预热池和 JSON Patch CAS。
- Prometheus 指标、DryRun 计划和 Operator 审批门。
- 一键业务 Demo、最小并发验证、README 最终实测数据。
- 后续模块学习文档及最终面试问答手册。
