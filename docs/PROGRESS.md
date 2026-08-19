# sandboxd 持续开发进度

> 本文件是跨会话进度快照。最高目标以 ../GOAL.md 为准，Phase 2 细节以 12-Agent层实现计划.md 为准。

## 当前状态

Phase 1 已完成并保留；Phase 2 外部告警诊断 Agent 正在进行。

当前分支：

    codex/phase2-langgraph-agent

当前里程碑：

    M1：Go 结构化 Kubernetes Diagnostic API

## Phase 1 已完成基线

- WSL2 + 单节点 kind + gVisor + Calico，已有 Starting gVisor 真实证据。
- restricted Pod 安全基线、短时 Token、只读 RBAC、NetworkPolicy 正反路径。
- Manager、Exec、Informer/Lister、Workqueue、预热池、JSON Patch CAS。
- Prometheus 指标、Deployment scale server-side DryRun 和 Agent/Operator 双 Token 审批门。
- 全项目 Go test/vet/build 和完整 Demo 已通过。
- 学习文档、面试问答、踩坑与 Phase 1 evidence 已完成。

Phase 2 不得破坏这些证据和接口。

## Phase 2 目标摘要

- Python agentd + LangGraph StateGraph。
- 真实用户态 Prometheus/Alertmanager 外部告警。
- OpenAI-compatible Live LLM 和确定性 Replay。
- Prometheus Query Tool。
- Go 结构化 Kubernetes Diagnostic API，查询在 gVisor 沙箱中发生。
- Pod Log/ConfigMap 间接 Prompt Injection。
- Python Policy、Go Tool Policy、RBAC、审批门纵深拒绝。
- 结构化诊断或 Pending Deployment Scale Plan。
- 脱敏 Trace、最小验证、学习/面试/踩坑文档和 GitHub 维护。

## 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| M0 | 已完成 | 目标、计划、AGENTS、PROGRESS、分支和版本策略 |
| M1 | 进行中 | Go 结构化 Kubernetes Diagnostic API |
| M2 | 未开始 | agentd、LangGraph、Live/Replay、三个工具 |
| M3 | 未开始 | Prometheus/Alertmanager 与故障 Fixture |
| M4 | 未开始 | 完整 Live/Replay 链路与拒绝矩阵 |
| M5 | 未开始 | Evidence、README、学习和面试文档 |

## 本轮已经完成

- 从 agent/environment-gvisor 的干净提交 9865955 创建 codex/phase2-langgraph-agent。
- 重新阅读 GOAL、全局计划、旧 Agent 计划、PROGRESS、AGENTS 和当前 API/RBAC/NetworkPolicy。
- 将 docs/12-Agent层实现计划.md 改为 Python agentd + LangGraph + Go 可信边界的权威规格。
- GOAL.md 增加 Phase 2 当前目标、非目标、完成证据和恢复顺序。
- AGENTS.md 增加 Phase 2 安全、证据和 Git 纪律。
- docs/00-实现计划.md 增加 Phase 2 总览。
- 确认现有沙箱镜像没有 kubectl；计划改为在 gVisor 内用固定 HTTPS 请求访问 Kubernetes API，避免扩大镜像和任意命令面。
- 确认 Python 3.12.3、系统无 pip、用户态 uv 可用；采用 pyproject.toml + uv.lock，不使用 sudo。
- 固定并验证 Python 直接依赖可解析：langgraph 1.2.11、langchain-core 1.5.6、langchain-openai 1.5.2、fastapi 0.141.1、uvicorn 0.52.4、httpx 0.28.1。
- 固定 Prometheus 3.14.0 与 Alertmanager 0.34.0 的官方 linux-amd64 SHA256。
- 在踩坑文档增加“系统 Python 无 pip”和“LangSmith 传递依赖不等于启用 tracing”。

## 已知事实与风险

- 当前 sandboxd 通用 Exec 接收任意 argv，Python 白名单不能作为最终边界，因此 M1 必须新增 Go 结构化诊断 API。
- 当前 sandbox-reader 是只读 ClusterRole，能读取 Pod/Log/ConfigMap/Event/Deployment，但不含 Secret 和写权限，可复用。
- sandboxd-target 可被现有 Plan 服务接受，可用于 CrashLoop Demo 和 Pending Scale Plan。
- Live 模型是否服从间接注入具有概率性；最多运行 3 次并如实记录，Replay 不冒充 Live。
- 本地 Codex apply_patch helper 因 WSL 缺少 bubblewrap 无法启动；当前用生成的 Git patch 修改仓库。该问题不影响项目运行，禁止为此安装系统组件或使用 sudo。

## 资源与秘密约束

- kind 单节点；Agent Demo pool=1、worker=1、一次一条告警。
- 可用内存接近 2 GiB、持续 swap 或 WSL/Docker 异常时立即停止。
- 默认不使用 sudo。
- Operator Token 不传给 agentd。
- 不提交任何密码、Token、API Key、认证头、ServiceAccount Token 或 kubeconfig。
- 只清理本次确认创建的进程和 sandboxd-target。
- 不触碰仓库外简历、秘密目录和无关文件。

## 下一步

1. 实现 internal/diagnostic 的只读 operation、参数验证和 Kubernetes API 路径构造。
2. 为拒绝 operation、namespace、名称和 tailLines 写最小表驱动测试。
3. 接入 POST /api/v1/sandboxes/{id}/diagnostics/kubernetes Handler。
4. 运行 Go test/vet/build，更新踩坑与进度。
5. 提交并推送 M1。

## 恢复工作时

1. 阅读 GOAL.md。
2. 阅读 docs/00-实现计划.md。
3. 阅读 docs/12-Agent层实现计划.md。
4. 阅读本文件。
5. 阅读 AGENTS.md。
6. 查看 git status、当前分支和最近提交。
7. 只继续“下一步”的第一项未完成工作。
