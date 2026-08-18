# sandboxd 持续开发进度

> 本文件是跨会话进度快照。每完成一个可验证步骤就更新它，避免上下文压缩后重复工作或偏移目标。目标和边界以 `../GOAL.md` 为准。

## 当前阶段

阶段 2：实现 ServiceAccount/RBAC、Pod Security Admission 和 Calico NetworkPolicy，形成最小权限闭环。

## 已完成

- 创建公开 GitHub 仓库：`https://github.com/chx739/sandboxd`，当前开发分支为 `agent/environment-gvisor`。
- 固化 `GOAL.md`、`AGENTS.md` 和本进度文件，建立跨上下文续作协议。
- 完成 WSL2 用户目录工具链：Go 1.26.5、kind 0.31.0、gVisor release-20260810.0。
- 创建 Kubernetes 1.35.0 单节点 kind，containerd 2.2.0 注册 `io.containerd.runsc.v1`，完整挂载新版 gVisor 包。
- 安装 Calico 3.32.0；节点和 CNI 组件 Ready。
- 真实运行 gVisor smoke Pod：`RuntimeClass=gvisor`，Pod 内 `dmesg` 出现 `[0.000000] Starting gVisor...`。
- 完成阶段 0 可复现脚本、资源安全门、学习文档和验证记录，提交 `3e8cb23` 已推送。
- 初始化 Go module `github.com/chx739/sandboxd`，依赖限定为 Kubernetes API 库。
- 实现 `internal/sandbox/BuildPod`，集中设置 gVisor、非 root、seccomp、只读根、资源上限、deadline、有界 emptyDir 和受控 projected token。
- 安全基线测试、projected token/卷测试、`go vet` 和 `go build` 均通过。
- 完成 `docs/02-Pod安全基线.md`。

## 正在进行

- 为 sandbox namespace 添加 Pod Security Admission `restricted` 标签。
- 创建 `sandbox-reader` ServiceAccount、排除 secrets 的只读 RBAC。
- 创建 Calico 可执行的默认拒绝、DNS 和 API Server 出站策略。

## 紧接着做

1. 编写 namespace、ServiceAccount、ClusterRole/RoleBinding 清单。
2. 用 `kubectl auth can-i` 验证读允许、写拒绝、secrets 拒绝。
3. 编写默认拒绝和最小允许 NetworkPolicy。
4. 用两个极小临时 Pod 验证 DNS/API 允许和普通外网拒绝。
5. 完成 `docs/03-ServiceAccount与RBAC.md` 与 `docs/04-NetworkPolicy与CNI.md`。
6. 更新实测证据、提交并推送，然后进入 Manager/Exec。

## 资源与安全约束

- kind：单 control-plane 节点；当前节点容器稳定后约占 1.1 GiB。
- 预热池默认：2。
- 并发验证默认：5；资源确认充足后上限 10。
- 可用内存接近 2 GiB、持续使用 swap 或 WSL/Docker 异常时停止测试。
- sudo 只用于经过确认、无法由普通用户完成的最小系统操作；密码不进入命令、脚本、日志或 Git。
- 只清理名称和来源都能确认属于 sandboxd 的容器、集群、网络和临时文件。

## 尚未开始

- Manager、Exec、HTTP API。
- Informer/Workqueue、预热池和 JSON Patch CAS。
- Prometheus 指标、DryRun 计划和 Operator 审批门。
- 一键业务 Demo、最小并发验证、README 最终实测数据。
- 后续模块学习文档及最终面试问答手册。
