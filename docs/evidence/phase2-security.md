# 阶段 2 验证记录：PSA + RBAC + NetworkPolicy

验证日期：2026-08-18

## 验证对象

- namespace：`sandboxd-demo`，PSA enforce/audit/warn 均为 restricted；
- ServiceAccount：`sandbox-reader`；
- CNI：Calico 3.32.0；
- 临时 Pod：gVisor、100m CPU limit、64 MiB memory limit；
- 网络策略：default-deny、allow-dns、allow-apiserver。

## RBAC 结果

```text
RBAC: get pods --all-namespaces -> yes
RBAC: create pods --namespace sandboxd-demo -> no
RBAC: get secrets --all-namespaces -> no
RBAC: create pods --subresource=exec --namespace sandboxd-demo -> no
```

这证明 Agent 可以读取诊断所需的 Pod 信息，但不能创建工作负载、读取凭证或进入其他 Pod。拒绝来自 API Server RBAC，不依赖提示词或应用层判断。

## 真实 token 与网络正向结果

临时 Pod 关闭自动 token，显式投影 1 小时 ServiceAccount token、CA 和 namespace。在 Pod 内使用该 token 访问：

```text
https://kubernetes.default.svc/api/v1/namespaces/sandboxd-demo/pods
```

实测：

```text
NetworkPolicy + token + RBAC: 集群内读取 Pod -> HTTP 200
```

这一个结果同时覆盖 DNS 解析、Calico DNS allow、Service 到真实 endpoint 的网络路径、TLS CA、projected token 和 RBAC 读权限。

## 网络拒绝结果

同一 Pod 访问 `https://example.com`：

```text
curl: (28) Connection timed out after 3002 milliseconds
NetworkPolicy: https://example.com -> 已按预期拒绝
```

如果 Calico 没有执行策略，curl 会成功，验收脚本会反向报错。因此这不是“存在 NetworkPolicy 对象”这种间接证据，而是真实数据路径的拒绝结果。

## API Server endpoint

脚本通过 EndpointSlice 动态得到本次 kind endpoint：

```text
172.19.0.2:6443
```

这个地址只记录为一次实测输出，不写死在仓库模板中。

## 资源和清理

- 验证前 WSL 可用内存约 5.6 GiB，swap 基本未使用；
- 只创建一个临时测试 Pod；
- 脚本退出时精确删除 `sandboxd-demo/security-smoke`；
- 未使用 sudo，未修改宿主防火墙或路由。

## 复现

```bash
./hack/check-resources.sh
./hack/verify-security.sh
```
