# 学习实验台账

> 本文件只记录可核对的学习实验状态。**预测不等于实测，现有正向证据不等于删除保护后的失败证据。** 本轮文档整理没有修改运行时代码、启动集群或补跑重型 E2E；已有结果引用 2026-09-01 的 [main 全量回归](phase9-full-regression.md)。

## 状态定义

| 状态 | 含义 | 面试中可以怎样说 |
|---|---|---|
| 已亲手执行 | 按实验步骤做了变更或负向操作，有命令、观察结果和恢复记录 | “我实际做过这个实验，结果是……” |
| 有等价证据 | 没有按学习路径改坏代码，但单测或 E2E 已验证同一安全不变量 | “现有测试验证了边界；我没有声称做过删除保护的实验” |
| 部分证据 | 只验证正常保护生效，未验证移除保护后的故障 | “这是预期风险，破坏实验仍待执行” |
| 尚未执行 | 只有假设和计划 | 只能讲推理，不能讲实测结果 |

## 当前总览

| ID | 学习实验 | 当前状态 | 已有证据或缺口 |
|---|---|---|---|
| EX-01 | 把 `allowPrivilegeEscalation` 改为 `true` | 有等价证据 | `internal/sandbox/spec_test.go` 会拒绝该配置；2026-09-01 Go 测试通过。尚未在学习分支实际改坏并观察失败。 |
| EX-02 | 删除 JSON Patch 的 `test` 后并发 Claim | 部分证据 | 全量回归实测 5 请求得到 5 个唯一 ID、记录 6 次冲突，证明当前 CAS 生效；尚未删除 `test` 观察重复认领。 |
| EX-03 | 让 informer handler 直接执行副作用 | 尚未执行 | 当前只验证 handler 入队、Worker Reconcile 的正确结构；没有故意制造阻塞或过期对象实验。 |
| EX-04 | Propose 后修改 Deployment，再 Approve | 已亲手执行 | 全量回归实际得到 `409 / stale`，Deployment 未按旧 Plan 执行。 |
| EX-05 | 绕过 Python Policy，验证 Go Policy/RBAC | 有等价证据 | 全量回归直接调用 Go 危险 operation 得到 `tool-policy 403`，通用 Exec DELETE 得到 RBAC `403 Forbidden`。未通过删除 Python 代码来验证，因为直接绕过更能证明后端独立。 |
| EX-06 | 错误 SHA、`..`、symlink 文件负路径 | 有等价证据 | `agentd/tests/test_files.py` 覆盖错误 `expectedSha256`、路径逃逸和 symlink；2026-09-01 frozen 22 tests 通过。尚未单独保存人工命令输出。 |

## 已有结果记录

### EX-04：stale Plan

- 基线：2026-09-01 main 全量回归；
- 操作：创建待审批 Plan，改变目标 Deployment 的 `resourceVersion`，再审批旧 Plan；
- 预测：Approve 必须拒绝旧对象快照，不能继续执行；
- 实际结果：`409 / stale`；
- 安全意义：DryRun 只验证提议当时的请求，UID/resourceVersion 复核负责审批到执行之间的 TOCTOU；
- 证据：[phase9 §Phase 1 E2E / Approval](phase9-full-regression.md)；
- 恢复：由 E2E 精确恢复目标和项目资源，残留审计通过。

### EX-05：绕过 Agent 前置策略

- 基线：2026-09-01 Agent Replay E2E；
- 操作一：不经过 Python Tool Dispatcher，直接向 Go 结构化诊断接口提交危险 operation；
- 实际结果一：HTTP `403`，`denyLayer=tool-policy`；
- 操作二：通过 Phase 1 通用 Exec 直接向 Kubernetes API 发 namespace DELETE；
- 实际结果二：Kubernetes RBAC `403 Forbidden`；
- 安全意义：Python Policy 是早拒绝和模型反馈层，不是最终授权；后端边界可独立拒绝；
- 证据：[phase9 §Agent Replay](phase9-full-regression.md)、[phase8 拒绝矩阵](phase8-agent-alert.md)；
- 边界：这不是“删除 Python 白名单后重新跑”的代码破坏实验，因此状态仍标为等价证据。

### EX-06：文件负路径

- 基线：`FileWorkspaceTest`；
- 操作：覆盖已有文件时省略或提供错误 SHA；提交 `../escape`、绝对路径和 symlink；
- 预测：分别在 CAS、路径或 symlink 检查处拒绝；
- 实际结果：对应 `assertRaises` 全部通过；
- 证据：`agentd/tests/test_files.py` 和 [phase9 静态验证](phase9-full-regression.md)；
- 边界：这是自动化负向测试，不冒充人工学习分支实验。

## 待执行实验模板

后续真正执行 EX-01、EX-02 或 EX-03 时，为每个实验补齐下面字段：

~~~text
实验 ID：
日期与基线 commit：
学习分支：
假设：
只改了什么：
最小执行命令：
实际输出摘要：
结果是否符合预测：
原因解释：
恢复方式与恢复后 git status：
是否创建集群/容器/临时文件，如何确认已清理：
~~~

安全要求：一次只做一个实验；优先单测和假客户端；需要真实环境时先检查资源；不得对 `main`、真实主机、非项目集群或用户数据做破坏；不得用 `git reset --hard` 清理学习结果。
