# Phase 9：main 全量回归与 E2E 证据

测试日期：2026-09-01。

测试提交：main 的 2c47579（文档与 Phase 1–4 完整实现）。

## 测试范围

本轮按以下顺序串行执行：

1. Git、硬件、Docker、kind context、系统 Pod、端口和残留基线；
2. Go/Python/Shell/JSON/Markdown 静态全量检查；
3. Phase 1 gVisor、安全、Manager/Exec、Pool/CAS/Metrics、Approval E2E；
4. Prometheus -> Alertmanager -> agentd 的确定性 Replay E2E；
5. Linux SSH Connector 与文件工具 Replay E2E；
6. namespace、Pod、端口、临时目录、进程和 Docker 容器残留审计。

没有增加并发规模，没有使用 sudo，没有创建第二个集群。

## 资源基线

测试开始前：

~~~text
CPU: 16
可用内存: 7097 MiB
已用 swap: 0 MiB
Linux 根盘可用: 937 GiB
Windows 盘可用: 195 GiB
~~~

项目 kind control-plane 初始处于 Exited。先核对：

~~~text
name=/sandboxd-control-plane
image=kindest/node:v1.35.0@固定摘要
kindCluster=sandboxd
role=control-plane
~~~

确认归属后只启动该容器。minikube 与 minio-tutorial 两个停止容器未触碰。节点、Calico、CoreDNS、API Server 和 local-path 均恢复 Ready。

## 静态全量检查

以下命令全部通过：

~~~text
go test -p 1 ./...
go vet ./...
go build -p 1 ./...
Python compileall
Python unittest: 22 tests
全部 tracked Shell: bash -n
全部 tracked JSON: python -m json.tool
全部本地 Markdown 链接
git diff --check
新增 diff 秘密模式扫描
~~~

Python 输出：

~~~text
......................
Ran 22 tests
OK
~~~

## Phase 1 E2E

执行：

~~~bash
./hack/demo.sh
~~~

### 安全边界

~~~text
RBAC get pods: yes
RBAC create pods: no
RBAC get secrets: no
RBAC pods/exec: no
集群内使用 projected token 读取 Pod: HTTP 200
访问 example.com: timeout，按预期被 NetworkPolicy 拒绝
~~~

NetworkPolicy 负向路径中的 curl exit 28 是预期成功证据，不是测试失败。

### gVisor

~~~text
RuntimeClass: gvisor
[   0.000000] Starting gVisor...
~~~

证据来自 Pod 内 dmesg，不是仅检查 RuntimeClass YAML。

### Manager 与 Exec

~~~text
未授权请求: 401
Create + informer Ready: 201
Exec success: exitCode=0
Exec failure: exitCode=7，未错误 fallback
Exec timeout: 504，Pod 已删除
Delete: 204，managed Pod=0
direct acquire=2, exec=3, timeout=1
~~~

### Pool、CAS 与 Metrics

~~~text
Pool target: 2 Ready idle
Concurrent Claim: 5 requests, 5 unique IDs
Claim source: pool=2, direct=3
Release 后 idle 恢复到 2
claim_conflicts=6
runtime=gvisor, busy=0
~~~

### DryRun 与审批

~~~text
DryRun 前后 replicas 保持 0
Agent approve: 401
Operator list/approve: 200
批准后 replicas: 0 -> 1
目标 resourceVersion 改变后 approve: 409 / stale
reject 后重复 approve: 409
~~~

Phase 1 脚本最终返回 0，并确认临时 workload 已清理。

## Agent Replay E2E

执行：

~~~bash
./hack/run-agent-demo.sh
~~~

结果：

~~~text
Prometheus -> Alertmanager -> agentd Task: succeeded
Prometheus query: executed
gVisor Kubernetes read: executed
Pod Log injection: entered Trace
delete_namespace: agent-policy denied
Go structured operation: tool-policy 403
通用 Exec DELETE: RBAC Forbidden 403
Agent approve: 401
Plan: pending
Deployment replicas: remained 1
Sandbox dmesg: Starting gVisor
~~~

本地完整 evidence：

~~~text
.cache/agent-demo-evidence/e578ec6b0337414496157e04930d9ac3
~~~

该目录不提交 Git；它是 deterministic-policy-case Replay，不冒充 Live。

## Linux SSH 与文件工具 Replay E2E

执行：

~~~bash
./hack/run-linux-agent-demo.sh
~~~

一次性 Target：

- 基础镜像 ID 在运行前核对；
- 0.25 CPU、192 MiB、64 PID、只读根；
- 随机 localhost SSH 端口；
- strict host key；
- 独立低权限用户，无 sudo；
- authorized_keys command 与 sshd ForceCommand 双重收口；
- 测试结束删除容器、Key 和临时配置。

结果：

~~~text
任意 SSH 命令负向路径: exit 126
linux_read: success
Linux Log injection: entered Trace
write_file/read_file: success
task Workspace isolation: success
文件正文未进入 Trace/Session
原 Prometheus/Kubernetes/gVisor/Policy/RBAC/Approval 链继续通过
~~~

本地完整 evidence：

~~~text
.cache/agent-demo-evidence/65d8b5b404a7411e8c28cf97eb502555
~~~

## Live LLM 状态

本轮准备使用用户此前提供的仓库外 DeepSeek Key 运行一次 Live E2E，但没有实际发送请求。

原因：Live 会把告警、Pod Log/ConfigMap 和工具上下文发送到外部 DeepSeek 服务。用户授权使用 Key，不等于明确授权把本项目/集群数据发送到该目的地。安全审查在创建进程前拒绝了命令，因此：

- 没有向 DeepSeek 发送本轮项目数据；
- 没有显示、记录或提交 Key；
- 没有把 Replay 结果标记为 Live；
- Live 是本轮唯一未执行项，等待用户对数据外传作单独明确授权。

历史脱敏 Live 证据继续保留在 phase8-agent-alert.md，不受本轮影响。

## 最终残留审计

~~~text
sandboxd-target namespace: absent
sandboxd-demo managed Pod: 0
8080/8090/9090/9093: free
sandboxd/prometheus/alertmanager 业务进程: 0
/tmp/sandboxd-linux-demo.*: 0
/tmp/sandboxd-agent-workspace.*: 0
E2E 结束首次审计的运行容器: sandboxd-control-plane only
E2E 结束首次审计可用内存: 6280 MiB
swap: 0 MiB
Git 工作区（记录前）: clean
~~~

项目 kind control-plane 是测试前已存在并经标签核实的资源。完成报告推送后，将本轮启动的 control-plane 停回测试前的 Exited 状态；最终运行容器为 0、可用内存恢复到 7037 MiB、swap 仍为 0。其他用户的停止容器没有修改。

## 结论

除需要单独数据外传授权的 Live LLM 外，main 的本地静态测试和 Phase 1–4 真实 E2E 全部通过。gVisor、RBAC、NetworkPolicy、Exec、Pool/CAS、Metrics、DryRun/Approval、Agent Replay、受限 SSH 和文件工作区均有本轮实际输出，不依赖历史结果宣布成功。
