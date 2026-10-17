# Phase 9 工作区恢复实施记录

日期：2026-10-17。基线：281111d；V1 分支：codex/workspace-nfs-v1。当前为 M0/M1，完整目标 active，未完成。

## 已执行的纯逻辑验证

| 模块 | 结果 | 原始证据 |
|---|---|---|
| DurableJournal | 8/8 通过 | phase9-durable-tests.txt |
| DurableJournal + RecoveryLedger | 20/20 通过，最终定向0.594s | phase9-recovery-tests.txt |
| Go 文件 helper | 9 个顶层测试通过，含 race detector | phase9-workspace-helper-tests.txt |
| Go workspace Pod/PV/PVC 构造及 sandbox 回归 | 通过 | phase9-go-foundation-tests.txt |
| Go 全仓库回归 | 通过 | phase9-go-regression-tests.txt |
| Go 持久 JSONL、归属状态机、初始化 helper、旧路径隔离及相关回归/race | 通过 | phase9-go-authority-tests.txt |
| Python 完整离线回归 | 110/110通过，295.970s | phase9-python-regression-native-tests.txt |
| Go生命周期、物理终止条件、文件/HTTP/配置相关race与回归 | 通过 | phase9-go-workspace-http-final-tests.txt、phase9-go-lifecycle-regression-tests.txt |
| Python远程文件、未知写入停止、旧文件/Loop/Replay定向 | 17/17通过，0.557s | phase9-remote-files-tests-final.txt |
| 第三批完整Python离线回归 | 118/118通过，276.228s | phase9-python-remote-regression-tests.txt |

这些是原生 Linux 临时目录与构造函数测试，不是真实 NFS、真实 Pod 故障或真实模型验证。20/20 包含前述 8 项，不可累加为 28 项。

## 环境核对

- Ubuntu 22.04.5；12 CPU；资源检查时可用内存 21,887 MiB，已用 swap 0；Linux 根盘可用 933 GiB。
- Docker Desktop 已正常启动，Server 29.8.0；Docker socket 需在允许访问本地服务的执行环境使用。
- 原有 sandboxd-rag、sandboxd-logs、minikube 等容器未启动/清理；不操作旧卷。
- 已使用原有脚本下载并校验 Go1.26.5、kind0.31.0、runsc release-20260810.0；目录 `/home/hx/.local/share/sandboxd-tools`。
- Go 依赖/编译缓存分别为 `/tmp/sandboxd-workspace-go-mod`、`/tmp/sandboxd-workspace-go-build`；这是可重建缓存，不能用于控制状态持久化。
- 本项目 kind-sandboxd 单节点已创建，固定 kindest/node:v1.35.0 digest 沿用仓库；Calico Ready，原有 gVisor smoke 通过。
- 官方 registry.k8s.io 的 Ganesha v6.5/v4.0.8 清单及拉取均因 asia-east1-docker.pkg.dev TCP 超时失败，不解释为镜像不存在。
- 已从官方 Debian bookworm-slim 固定 amd64 digest 构建 Ganesha4.3-2；只更换可复现打包方式，保留 NFSv4.1/VFS 路线。实际镜像身份记录在组件 JSON，候选配置通过只读挂载注入。
- 用户已明确授权仅给可信 NFS 存储容器增加 CAP_DAC_READ_SEARCH；未授权 privileged、SYS_ADMIN 或 seccomp unconfined，业务 Pod 安全配置不变。
- 外部 LLM/Jev 调用：0。

## 真实本地服务组件探测

`phase9-nfs-component.json`：固定合成工作区，真实 gVisor RuntimeClass、UID/GID65532、独立 PVC、节点 nfs4/vers=4.1、文件 helper 读写、服务端 SHA256 一致、路径/CAS 拒绝均通过。不是默认 Agent RPC，也未测试任务自动接续。

发现并修复的环境问题：

1. 导出父目录0700时，root_squash 下 kubelet无法遍历子路径挂载；父目录改0711，子目录仍0700。
2. gVisor可信 gofer在主机挂载上的访问会受 root_squash 影响，最初 helper/ls 均返回权限不足。保留 root_squash，将被映射的匿名身份设为非 root 的65532；待权限缓存更新后真实读写通过。未改为 no_root_squash，未增加业务 Pod 权限。
3. 初次渲染 List 时子对象缺 TypeMeta，kubectl校验拒绝；显式填入Kind/APIVersion后通过，不关闭校验绕过。

本轮为同 UID 单租户 MVP，隔离依赖专属挂载、网络边界与 Go 所有权校验，不能声称 AUTH_SYS 或 Unix UID提供了独立多租户安全。

探测结束后，正常删除合成 Pod（未使用force），保留PVC/PV和数据卷；服务端 component-report.md 的SHA256仍为 a31625d26c5418620aadb9ccc1ba7ab3526f20778796bc60c4a7f3d88190aea9，与组件报告一致。这只是手动清理后的文件持久性，仍不等于自动接续验收。

## 正式验收矩阵

文档43的 V1-01～V1-14、V2-01～V2-10 **全部尚未验收**。纯逻辑测试只作为相关不变量的先行证据，不能提前勾选真实集成项目。

Go持久归属、受限HTTP文件接口和正常释放后换Pod的组件路径已经真实通过；当前默认Python Runner仍未迁移，TaskStore/Session自动接续与完整预算仍待接线，14项V1正式验收未通过。V2主链路未启动。

## 第二批实现与测试执行环境（7b244bf时）

- Go 独立日志与 WorkspaceRegistry 已补注册/初始化/认领/绑定/停止/释放状态、代次、镜像身份约束和互斥；仍待接真实 Kubernetes 生命周期及公开受限接口。
- 新可信 workspace-init 二进制只初始化固定 ID 的私有目录，尚未部署维护 Pod；其单测使用本地 Linux 临时目录。
- 预热池不再认领/删除持久工作区 Pod；旧通用exec与无归属释放明确拒绝持久工作区，相关单测通过。
- 第一次全 Python 回归在受限执行环境停在 asyncio.to_thread 的线程唤醒阶段，被主动中断，原始部分输出保留 phase9-python-regression-tests.txt，不能作通过成绩。
- 该部分输出末尾追加了明确的INTERRUPTED标记以结束未写完的单测状态行；此前捕获内容保留，没有将中断伪报为测试通过。
- 最小无网络 asyncio线程复现也在受限环境无法退出，而允许本机socket的执行环境0.002秒结束；改在后者运行纯离线测试，110/110通过。未为测试修改应用逻辑或放宽业务沙箱配置。
- 当前草稿 [PR #2](https://github.com/chx739/sandboxd/pull/2) 依赖 PR #1，未合并；第一批提交 dbc02d2 已推送。后续继续追加小提交。

## 2026-10-18 第三批：真实Go生命周期和文件HTTP

- WorkspaceRuntime已连接可信维护Pod、专属PV/PVC、创建请求预留、早期Pod UID保存、Ready后实际写入/fsync健康检查、当前UID/代次文件访问及释放。
- 释放先持久化stopping；只在全部容器Terminated、RestartPolicyNever、NodeReady和近期Node Lease成立时保存终止证明，再精确删除/移除finalizer并released。对象消失、租约失效、调用超时都不冒充停止；确认后崩溃在移除finalizer窗口的重入也有单测。
- `phase9-go-runtime-component.json`：真实Go方法链、NFS/gVisor、初始化/写入/停止/账本重开/换Pod/读回/旧attempt拒绝通过，23.597秒。
- `phase9-go-http-component.json`：相同路线通过实际鉴权HTTP处理器（本机fixture服务）验证，18.400秒；包含真实Kubernetes/gVisor/NFS。两轮合成工作区与PV/PVC保留，业务/维护Pod正常清理。
- 上述耗时是整条组件流程单次耗时，不是Agent自动恢复RTO、P50/P95或生产性能结论；账本重开不是强杀agentd，生成的session/branch标签也不代表已经写入Python Session树。
- 新Go启动参数已接main，控制目录拒绝/tmp等临时路径。工作区接口Agent Token保护；文件请求中的绑定与操作分开解码，Go/执行侧双重校验。后端失败与权限拒绝不混为同一种deny。
- Python适配器只访问Go接口，不创建本地目录；未知写入不重试，Loop中断当前attempt。仍需Runner/Store实际注入，当前默认仍为旧后端。
- 第三批完整Python回归118/118通过，276.228秒，`phase9-python-remote-regression-tests.txt`；不是把上一轮110项成绩改名。耗时主要仍为Windows挂载盘上CLI子进程启动，不是在线文件接口延迟。

## 下一步

接Python TaskStore/Runner/Session的持久Task/attempt与自动恢复，原claim/loop/cleanup独立时钟、预算预留/Replay游标、取消与控制命令游标、未知写入hash核查及旧节点限制。之后迁移默认文件后端并执行14项V1完整验收，再启动V2。持续保存失败记录与真实结果，不使用已消费的模型预算。
