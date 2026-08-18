# 模块 03：ServiceAccount 与 RBAC

## 这个模块解决什么问题

沙箱中的 AI Agent 需要读取集群状态来诊断问题，但不应直接修改集群。ServiceAccount 解决“Pod 以谁的身份访问 API”，RBAC 解决“这个身份能对哪些资源执行哪些动作”。本项目用物理权限边界保证：即使 prompt injection 诱导 Agent 删除 Pod，API Server 也会拒绝。

## 项目里的最小实现

`deploy/rbac.yaml` 创建：

- `sandbox-reader` ServiceAccount；
- 只含 `get/list/watch` 的 ClusterRole；
- 把角色绑定到该 ServiceAccount 的 ClusterRoleBinding。

允许读取 Pod、日志、Service、Endpoint、ConfigMap、事件、常见 workload 和节点等诊断信息。刻意排除：

- `secrets`；
- `pods/exec`；
- `pods/attach`；
- `pods/portforward`；
- 所有 create/update/patch/delete 动作。

## 代码阅读顺序

1. `deploy/rbac.yaml`：身份、规则、绑定三者的关系。
2. `internal/sandbox/spec.go`：Pod 如何指定 SA 并手动挂载 token。
3. `hack/verify-security.sh`：允许和拒绝路径的真实验收。
4. `docs/evidence/phase2-security.md`：本机实测输出。

## 必须掌握的基础知识

### ServiceAccount 不是用户账号

ServiceAccount 是 Kubernetes 管理的工作负载身份，典型用户名是：

```text
system:serviceaccount:<namespace>:<name>
```

本项目身份为 `system:serviceaccount:sandboxd-demo:sandbox-reader`。Pod 中的短期 JWT 经过 API Server 验证后映射到这个身份。

### Role 与 ClusterRole

- Role 的规则只在一个 namespace 内生效。
- ClusterRole 可以描述集群级资源，也可以被不同 namespace 的 RoleBinding 复用。
- RoleBinding 把权限限制在自己的 namespace；ClusterRoleBinding 让权限在集群范围生效。

本 Demo 要让 Agent 查看 nodes、namespaces 和跨 namespace 工作负载，所以选择 ClusterRole + ClusterRoleBinding。生产多租户系统通常应进一步缩到 namespace 级。

### RBAC 四元组

判断一次请求时，至少看：subject、verb、resource/subresource、namespace。`get pods` 与 `create pods/exec` 是两条完全不同的权限；URL 看起来像“进入 Pod”，RBAC verb 实际是 create。

## 为什么“只读”不等于安全

读取 Secret 可以直接获得 token、密码或证书，效果可能比修改一个普通对象更严重。因此规则没有因为 verb 是 get 就允许 secrets。

同理，pods/exec、attach、portforward 能进入其他工作负载或访问其网络，属于横向移动能力，必须排除。

## 为什么使用短期 projected token

Pod 关闭默认自动挂载，再显式投影 3600 秒 token。kubelet 会在 token 过期前轮转，应用仍通过标准路径使用 in-cluster config。这样凭证来源、受众和寿命都能在 PodSpec 中审计。

## 考虑过但没有采用的方案

- 给 Agent `view` 内置 ClusterRole：简单，但可能包含本项目不想开放的资源，规则也不够直观。
- 使用 cluster-admin 再靠提示词约束：提示词不是权限边界，prompt injection 可以绕过。
- 为每次命令生成 kubeconfig：增加凭证管理复杂度，标准 projected token 已足够。
- 把宿主高权限 token 放进沙箱：完全破坏读写分离，审批门也失去意义。

## 常见错误

- 创建 ClusterRole 却忘记 Binding，规则存在但身份没有权限。
- 误以为 `pods/exec` 使用 get，实际授权时通常是 create subresource。
- 允许读取 secrets 后仍把角色称为“安全只读”。
- ServiceAccount 写错 namespace，Binding 指向了另一个同名身份。
- 只检查 `kubectl auth can-i`，没有用 Pod 内真实 token 发请求。

## 面试高频问答

**问：为什么不让 Agent 直接写，再做审计？**

答：审计只能事后发现，不能阻止破坏。本项目先用 RBAC 物理拒绝写操作，合法写入只能经过宿主侧 DryRun 和 Operator 审批门。

**问：如何证明规则真的生效？**

答：一方面用 `kubectl auth can-i --as` 验证规则，另一方面从 gVisor Pod 内使用真实 projected token 调 API。实测读取 Pod 返回 200，而创建 Pod、读取 Secret 和 exec 都是 no。

**问：ClusterRoleBinding 会不会太大？**

答：对生产多租户系统确实偏大；这里是单租户诊断 Demo，需要展示 nodes 和跨 namespace 状态。文档明确标注边界，生产化应改成租户级 RoleBinding 和资源白名单。

## 验证命令

```bash
./hack/verify-security.sh

kubectl auth can-i get pods --all-namespaces \
  --as system:serviceaccount:sandboxd-demo:sandbox-reader
kubectl auth can-i get secrets --all-namespaces \
  --as system:serviceaccount:sandboxd-demo:sandbox-reader
```

## 一分钟项目讲法

我把沙箱身份固定为 `sandbox-reader` ServiceAccount，只允许 get/list/watch 诊断资源。Secret 虽然是读操作，但能泄露集群凭证，所以明确排除；pods/exec、attach、portforward 在 RBAC 中属于 create，而且能横向移动，也排除。Pod 使用一小时 projected token，而不是隐式默认 token。验收不仅跑 `can-i`，还从真实 gVisor Pod 内用该 token 请求 API：读返回 200，写和敏感读被 API Server 拒绝。
