# sandboxd 持续开发进度

> 本文件是跨会话进度快照。每完成一个可验证步骤就更新它，避免上下文压缩后重复工作或偏移目标。目标和边界以 `../GOAL.md` 为准。

## 当前阶段

阶段 0：搭建 WSL2 下的固定版本工具链和最小 kind + gVisor 环境。

## 已完成

- 创建公开 GitHub 仓库：`https://github.com/chx739/sandboxd`。
- 初始化 `main`，提交 README、最小实现计划和学习文档索引。
- 创建开发分支：`agent/environment-gvisor`。
- 确认环境为 WSL2 Ubuntu 24.04、systemd、cgroup v2、Docker Engine 和 containerd 可用。
- 确认宿主资源适合低资源 Demo：16 CPU、约 7.7 GiB 内存、2 GiB swap；采用单节点和小并发策略。
- 下载并校验 Go 1.26.5、kind 0.31.0 和官方 gVisor 发布包。
- 安装解压工具 `bzip2`，确认新版 gVisor 包同时包含 `runsc`、containerd shim 和 `gvisor-bin/` 辅助程序。

## 正在进行

- 将 Go、kind、gVisor 解压到用户目录，避免不必要的系统级安装。
- 记录 gVisor 的精确版本和校验信息。
- 把已验证的安装步骤整理为可重复执行的 `hack/install-tools.sh`。

## 紧接着做

1. 验证 `go version`、`kind version`、`runsc --version`。
2. 编写单节点 kind 配置，把完整 gVisor 包只读挂载进节点。
3. 在 kind 节点配置 containerd `runsc` runtime，创建 `RuntimeClass`。
4. 运行一个极小 Pod，确认真实 runtime 是 gVisor/runsc，并记录证据。
5. 安装固定版本 Calico，复查可用内存和集群状态。
6. 进入 PodSpec Builder 模块，同时编写 `docs/01-gVisor与容器隔离.md` 和对应安全基线文档。

## 资源与安全约束

- kind：单 control-plane 节点。
- 预热池默认：2。
- 并发验证默认：5；资源确认充足后上限 10。
- 可用内存接近 2 GiB、持续使用 swap 或 WSL/Docker 异常时停止测试。
- sudo 只用于经过确认、无法由普通用户完成的最小系统操作；密码不进入命令、脚本、日志或 Git。
- 只清理名称和来源都能确认属于 sandboxd 的容器、集群、网络和临时文件。

## 尚未开始

- Go 模块骨架和 PodSpec Builder。
- ServiceAccount/RBAC 和 Calico NetworkPolicy。
- Manager、Exec、HTTP API。
- Informer/Workqueue、预热池和 JSON Patch CAS。
- Prometheus 指标、DryRun 计划和 Operator 审批门。
- 一键 Demo、最小并发验证、README 实测数据。
- 各模块学习文档及最终面试问答手册。
