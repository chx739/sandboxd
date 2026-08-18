# 模块 04：NetworkPolicy 与 CNI

## 这个模块解决什么问题

RBAC 只约束 Kubernetes API，不会阻止沙箱访问公网、数据库或其他 Pod。NetworkPolicy 把网络边界收紧为默认拒绝，只放行 DNS 和 Kubernetes API Server，使不可信命令无法任意出网。

## 项目里的最小实现

`deploy/networkpolicy.yaml.tmpl` 包含三条可叠加策略：

1. `sandbox-default-deny`：拒绝所有 ingress 和 egress；
2. `sandbox-allow-dns`：只允许访问 kube-system 中带 `k8s-app=kube-dns` 标签的 Pod 53/UDP、53/TCP；
3. `sandbox-allow-apiserver`：只允许访问 API Server 实际 endpoint 的 TCP 端口。

`hack/apply-security.sh` 从 EndpointSlice 读取当前 endpoint，把 IP 和端口渲染到 `.cache/networkpolicy.yaml` 后应用。机器相关 IP 不提交 Git。

## 代码阅读顺序

1. `deploy/kind/config.yaml.tmpl`：关闭不实现 NetworkPolicy 的默认 kindnet。
2. `hack/install-calico.sh`：安装固定版本 Calico。
3. `deploy/networkpolicy.yaml.tmpl`：默认拒绝和两个最小例外。
4. `hack/apply-security.sh`：动态解析 API Server endpoint。
5. `hack/verify-security.sh`：允许集群 API、拒绝普通公网的对照实验。

## 必须掌握的基础知识

### CNI 与 NetworkPolicy 的关系

CNI 负责给 Pod 配置网络。Kubernetes 只定义 NetworkPolicy API 和语义，真正执行策略的是支持它的网络插件。kind 默认的 kindnet 能联网，但不执行 NetworkPolicy；因此“YAML apply 成功”不代表隔离生效。

本项目关闭默认 CNI，安装 Calico 3.32.0，让策略具有真实执行者。

### 策略是加法模型

一个方向只要被某条 policy 选中就进入受控状态，最终允许流量是所有匹配策略允许规则的并集。default-deny 先把允许集合设为空，DNS 和 API Server 策略再分别加入两个例外。

NetworkPolicy 没有显式 deny 规则；“拒绝”来自被选中后没有任何 allow 匹配。

### Ingress 与 Egress

- ingress：进入被选中 Pod 的流量；
- egress：从被选中 Pod 发出的流量。

本 Demo 的 sandbox 不提供被其他 Pod 调用的服务，所以 ingress 全拒绝。egress 只保留完成诊断所需的 DNS 与 API。

## 为什么放行真实 API Server endpoint

`kubernetes.default.svc` 是 Service ClusterIP，请求会经过 kube-proxy DNAT 到真实 endpoint。NetworkPolicy 在 Pod 网络路径中看到的目标可能已经是 DNAT 后地址，直接把 Service ClusterIP 写入 ipBlock 并不可靠。

kind 重建后 control-plane IP 可能变化，所以脚本读取 `discovery.k8s.io/v1 EndpointSlice`，而不是把 `172.x.x.x` 写死。Kubernetes 1.33 起传统 Endpoints API 已弃用，这里直接使用新 API。

## 为什么 DNS 需要单独放行

default-deny 会同时拦截 UDP/TCP 53。忘记 DNS 后，所有域名请求表现为解析超时，容易被误判为 API、证书或应用故障。本项目同时约束 DNS namespace、Pod label 和端口，避免把整个 kube-system 网络开放给沙箱。

## 考虑过但没有采用的方案

- 继续使用 kindnet：资源更少，但策略会静默不生效，无法作为真实安全证据。
- 允许所有公网 443：方便下载依赖，但基本失去 egress 隔离意义。
- 写死 control-plane IP：首次能跑，重建 kind 后容易失效。
- 只用 DNS 名称写 NetworkPolicy：原生 NetworkPolicy 不支持 FQDN 规则。
- Service mesh egress gateway：生产能力更强，但明显超出最小 Demo 范围。

## 常见错误

- CNI 不支持 NetworkPolicy，apply 成功却完全不拦截。
- default-deny 后忘记 DNS，所有域名都超时。
- 只允许 Service ClusterIP，没有考虑 DNAT 后 endpoint。
- 误以为策略按顺序匹配；实际上是声明式加法集合。
- 用一个已经缓存 DNS 或复用连接的进程做实验，得到误导结果。
- 只验证拒绝路径，忘记证明必要的 API 请求仍然可用。

## 面试高频问答

**问：为什么有 RBAC 还要 NetworkPolicy？**

答：RBAC 只管 API Server 授权，不能阻止 curl 外网或扫描其他 Pod。二者分别限制“能调用什么 Kubernetes API”和“网络能到哪里”。

**问：怎么证明 NetworkPolicy 不是纸面配置？**

答：我用 Calico 作为执行策略的 CNI，并从同一个临时 Pod 做正反实验：通过 DNS 和真实 endpoint 访问 API 返回 200；访问 example.com 在 3 秒连接超时后失败。

**问：为什么 policy 中没有 deny？**

答：Kubernetes NetworkPolicy 是 allow-list 和加法模型。Pod/方向被 policy 选中后，只允许所有匹配规则的并集；没有匹配就是拒绝。

## 验证命令

```bash
./hack/install-calico.sh
./hack/verify-security.sh
kubectl get networkpolicy -n sandboxd-demo
```

本机实测的关键输出：

```text
NetworkPolicy + token + RBAC: 集群内读取 Pod -> HTTP 200
curl: (28) Connection timed out after 3002 milliseconds
NetworkPolicy: https://example.com -> 已按预期拒绝
```

## 一分钟项目讲法

RBAC 只能约束 Kubernetes API，因此我用 Calico NetworkPolicy 再限制网络。策略先默认拒绝所有进出流量，只放行 CoreDNS 和 API Server。kind 的 API Server IP 每次可能变化，所以脚本从 EndpointSlice 读取真实 endpoint 后渲染策略，而不是写死 Service ClusterIP。验收采用同一 gVisor Pod 的正反实验：真实 token 读取 API 返回 200，访问普通公网则连接超时，证明必要能力保留且 egress 拒绝确实由 CNI 执行。
