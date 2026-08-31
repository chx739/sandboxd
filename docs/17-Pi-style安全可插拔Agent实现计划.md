# Phase 3：Pi-style 安全可插拔运维 Agent Runtime 实现计划

> 本文是 Phase 3 的权威实现规格。目标是解释并最小实现 Pi 的 Agent Runtime 设计哲学，而不是复刻 Pi Coding Agent。任何续作都必须先读 `../GOAL.md`、本文和 `PROGRESS.md`。

## 1. 一句话目标

在保留 Go sandboxd/gVisor 可信执行边界的前提下，用 Python 手写一个能真实运行的极简双层 Agent Loop，加入受信任内置插件、线性 Session-lite、steer/follow-up/cancel，并让现有告警诊断、Prompt Injection 拦截、Pending Plan 和审计链路继续成立。

## 2. 为什么做，为什么不重写 sandboxd

Pi 的核心价值不是某个模型或 UI，而是把 Transcript、Tool、事件、steer、follow-up 和 Session 分层。官方 Pi 默认让工具/扩展运行在宿主进程权限下，真正隔离由外部系统提供；本项目已有的 sandboxd、gVisor、RBAC、NetworkPolicy 和审批门正好承担这个可信边界。

因此 Phase 3 只重构 agentd 编排层。sandboxd 保持“不理解 LLM，只验证并执行结构化请求”的定位；第一阶段不修改 Go 接口。

## 3. 固定设计

```text
Alertmanager / REST API
          |
          v
TaskStore ---- SessionJournal(JSONL)
          |
          v
PiStyleAgent: messages + steer/follow-up queue + cancel
          |
          v
双层 Agent Loop
  外层：follow-up
  内层：model -> tool batch -> steer -> model
          |
          v
Trusted Plugin Registry
  prometheus / kubernetes-plan
          |
          v
Python Policy -> sandboxd -> gVisor/RBAC/Approval
```

### 3.1 Turn 定义

一次 Turn 等于“一次模型调用 + 该响应产生的一组顺序工具执行”。工具结果作为 ToolMessage 回灌；模型没有 Tool Call 时，当前内层循环准备结束。

### 3.2 steer 与 follow-up

- `steer`：运行中进入队列，在当前 Turn 完成后的安全点注入下一次模型调用；它不撤销已经发出的动作。
- `follow-up`：只有当 Agent 原本没有 Tool Call 和 steer、准备结束时才注入。
- `cancel`：取消运行任务；Runner 的 `finally` 必须在独立清理窗口释放已认领 Sandbox。

### 3.3 插件信任模型

第一版插件必须是代码中显式注册的内置插件。每个插件提供：

- `plugin_id`、`version`、用途说明；
- 一个或多个模型 Tool Schema；
- Tool 名称到执行函数的映射；
- 只用于审计和预检的 capability 声明。

插件不得直接获得 API Token、Operator Token、任意文件系统或任意网络能力。执行上下文只传入当前 sandbox ID 和已经构造好的最小客户端。

## 4. 文件结构

```text
agentd/app/
  runtime/
    control.py       steer/follow-up/cancel 状态
    loop.py          双层循环与事件
    session.py       append-only JSONL
  plugins/
    base.py          PluginManifest、ToolPlugin、PluginContext
    registry.py      受信任内置注册表
    prometheus.py    query_prometheus
    kubernetes.py    kubernetes_read、propose_plan
  runner.py          申请/释放 sandbox，组装 Runtime 和最终 Trace
  graph.py           临时兼容导入，后续可删除
```

## 5. 实现顺序

### M0：结构化记忆与测试基线

- 更新 GOAL、AGENTS、PROGRESS；
- 新建 Phase 3 分支；
- 记录当前 9 个 Python 测试和 Go 基线；
- 不启动 kind、Prometheus、Alertmanager 或 Live LLM。

### M1：插件注册表

- 定义最小插件接口与不可变 Manifest；
- 注册 Prometheus、Kubernetes/Plan 两个内置插件；
- 模型 Tool Schema 从 Registry 生成；
- 保留现有 Python Policy，并在 Trace 中记录 pluginId/version。

### M2：手写 Agent Loop

- 用双层循环替换 StateGraph；
- 保留最大轮数、工具次数、上下文裁剪、拒绝回灌和最终结构校验；
- 保持工具顺序执行；
- 原有 Replay 与取消释放测试必须通过。

### M3：Session-lite 与交互控制

- 每个 Task 对应一个 Session ID；
- Session 使用 0600 JSONL，Header、Transcript、Command、Result 分类型追加；
- API 支持查看 Session 摘要、steer、follow-up、cancel；
- 第一版 resume 只恢复线性消息并创建新的 Sandbox，不恢复旧进程和旧 Sandbox。

### M4：学习、面试和 Demo

- 写 Agent Runtime 学习文档：双层循环、事件、Session、插件、能力边界；
- 写面试问答：为什么不用完整 Pi、为什么插件不能获得宿主权限、steer/cancel 区别；
- 把真实开发坑追加到 `11-开发踩坑与排障.md`；
- 更新 README、学习路径和一分钟讲法。

## 6. 最小 API

```text
POST /api/v1/tasks/{taskId}/steer
POST /api/v1/tasks/{taskId}/follow-up
POST /api/v1/tasks/{taskId}/cancel
GET  /api/v1/sessions/{sessionId}
POST /api/v1/sessions/{sessionId}/resume
GET  /api/v1/plugins
```

写接口只接受 API Token；Alert Token 仍只能提交 Alert。正文保持小尺寸上限。取消和 steer/follow-up 都必须写入脱敏 Session 记录。

## 7. 非目标

- 不做 Session 树、fork、分支导航和长期记忆；
- 不做插件市场、动态安装、在线更新和任意第三方代码；
- 不做任意 Shell、文件编辑、宿主机直接执行；
- 不做 TUI、多 Agent、数据库、消息队列和分布式锁；
- 不追求流式 Token UI、复杂自动摘要和完整测试覆盖；
- 不改写 sandboxd 核心，不破坏 Phase 1/2 证据。

## 8. 完成判据

- 代码中能一眼看到 Pi 风格双层 Loop 和两个消息队列；
- 现有 Replay 告警诊断输出、注入拦截、Pending Plan、Trace 和 Sandbox 释放行为不退化；
- 插件列表可查询，模型工具来自 Registry，越权仍由多层 Policy 拒绝；
- steer/follow-up/cancel 有最小 API 和单测；
- Session JSONL 权限、脱敏、读取和最小恢复可验证；
- Go test/vet/build 与 Python 最小测试通过；
- README、学习文档、面试文档、踩坑和 PROGRESS 已更新并推送 GitHub。

## 9. 上下文恢复时的第一步

查看 `docs/PROGRESS.md` 的“Phase 3 下一步”，只继续第一项未完成里程碑。若实现与本文冲突，先更新本文并说明原因；触碰 GOAL 的非目标时必须停止并询问用户。
