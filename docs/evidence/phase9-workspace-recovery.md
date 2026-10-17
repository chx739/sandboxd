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

当前尚未接通默认运行：远程文件 RPC、Go 持久归属、TaskStore/Runner/Session 接续、真实 NFS 接管。V2 主链路未启动。

## 第二批实现与测试执行环境

- Go 独立日志与 WorkspaceRegistry 已补注册/初始化/认领/绑定/停止/释放状态、代次、镜像身份约束和互斥；仍待接真实 Kubernetes 生命周期及公开受限接口。
- 新可信 workspace-init 二进制只初始化固定 ID 的私有目录，尚未部署维护 Pod；其单测使用本地 Linux 临时目录。
- 预热池不再认领/删除持久工作区 Pod；旧通用exec与无归属释放明确拒绝持久工作区，相关单测通过。
- 第一次全 Python 回归在受限执行环境停在 asyncio.to_thread 的线程唤醒阶段，被主动中断，原始部分输出保留 phase9-python-regression-tests.txt，不能作通过成绩。
- 该部分输出末尾追加了明确的INTERRUPTED标记以结束未写完的单测状态行；此前捕获内容保留，没有将中断伪报为测试通过。
- 最小无网络 asyncio线程复现也在受限环境无法退出，而允许本机socket的执行环境0.002秒结束；改在后者运行纯离线测试，110/110通过。未为测试修改应用逻辑或放宽业务沙箱配置。
- 当前草稿 [PR #2](https://github.com/chx739/sandboxd/pull/2) 依赖 PR #1，未合并；第一批提交 dbc02d2 已推送。后续继续追加小提交。

## 下一步

将 Go 权威绑定接入物理终止证明、可信维护 Pod 和文件 RPC，再接 Python 生命周期及自动恢复。Runner接线时须保留原claim/loop/cleanup独立预算语义，不能简单用enqueue时间+120秒替代。持续保存失败记录与真实结果，不使用已消费的模型预算。
