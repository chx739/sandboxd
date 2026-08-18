# sandboxd 持续开发进度

> 本文件是跨会话进度快照。每完成一个可验证步骤就更新它，避免上下文压缩后重复工作或偏移目标。目标和边界以 `../GOAL.md` 为准。

## 当前阶段

阶段 3：实现 client-go Manager、等待 Pod Ready、删除和 remotecommand Exec，再接最小 HTTP API。

## 已完成

- 创建公开 GitHub 仓库并建立 `GOAL.md`、`AGENTS.md`、本进度文件的跨上下文续作协议。
- 完成 WSL2 用户目录工具链、Kubernetes 1.35.0 单节点 kind、gVisor release-20260810.0 和 Calico 3.32.0。
- 真实运行 gVisor smoke Pod，Pod 内 `dmesg` 出现 `[0.000000] Starting gVisor...`；阶段 0 提交 `3e8cb23` 已推送。
- 实现 PodSpec Builder，集中设置非 root、seccomp、只读根、资源限制、有界 emptyDir 和受控 projected token；测试/vet/build 通过；提交 `462506b` 已推送。
- namespace 启用 PSA restricted enforce/audit/warn；安全 Pod server dry-run 通过，故意不安全 Pod 被准入拒绝。
- 创建 `sandbox-reader` ServiceAccount 和只读 ClusterRoleBinding；允许 get pods，拒绝 create pods、get secrets、create pods/exec。
- Calico 策略默认拒绝 ingress/egress，只允许 CoreDNS 和动态 EndpointSlice 得到的 API Server endpoint。
- 在真实 gVisor Pod 内使用 projected token 读取 Pod API 返回 HTTP 200，访问 example.com 连接超时并按预期拒绝。
- 验收 Pod 限制为 100m CPU/64 MiB 并在脚本退出时删除；验证期间可用内存约 5.6 GiB，未使用 sudo。
- 完成 `docs/03-ServiceAccount与RBAC.md`、`docs/04-NetworkPolicy与CNI.md` 和阶段 2 证据记录。

## 正在进行

- 引入 client-go 0.35.x，与 Kubernetes API 库保持 minor 一致。
- 实现 Manager 的创建、缓存等待 Ready 和精确删除。
- 实现 WebSocket executor 优先、SPDY fallback 的超时 Exec。

## 紧接着做

1. 建立最小 kubeconfig/client 装配和 informer 工厂。
2. Manager 统一调用 `BuildPod`，禁止在生命周期代码里修改安全 PodSpec。
3. 等待 Ready 使用 informer/lister，而不是轮询 API Server。
4. Exec 返回 stdout、stderr、exit code；超时使用新 cleanup context 删除 Pod。
5. 实现基础 HTTP API 的 health、create/list/delete/exec，默认监听 127.0.0.1。
6. 完成 `docs/05-client-go与Exec.md`，进行一个极小真实命令演示后提交。

## 资源与安全约束

- kind：单 control-plane 节点；稳定后约占 1.1 GiB。
- 预热池默认：2；并发验证默认 5，资源确认充足后上限 10。
- 可用内存接近 2 GiB、持续使用 swap 或 WSL/Docker 异常时停止测试。
- sudo 仅用于无法由普通用户完成的最小系统操作；密码不进入命令、脚本、日志或 Git。
- 只清理名称和来源都能确认属于 sandboxd 的容器、集群、网络和临时文件。

## 尚未开始

- Manager、Exec、HTTP API 的具体实现。
- Informer/Workqueue、预热池和 JSON Patch CAS。
- Prometheus 指标、DryRun 计划和 Operator 审批门。
- 一键业务 Demo、最小并发验证、README 最终实测数据。
- 后续模块学习文档及最终面试问答手册。
