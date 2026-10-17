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
| 账本分阶段时钟、Session多attempt与既有模块定向 | 50/50通过，1.537s | phase9-attempt-clock-final-tests.txt |
| 分阶段时钟/Session批次完整Python回归 | 137/137通过，270.335s；后续追加边界见上述50项 | phase9-python-attempt-clock-regression-tests.txt |

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

文档43当前正式通过 **V1-01/02/03/05/07/08/09/12/13，共9/14**；04/06/10/11/14及全部V2尚未完成。机器可读证据清单见`phase9-v1-acceptance-progress.json`。纯逻辑/Fake证据不能提前勾选真实集成项目。

默认Python TaskStore/Runner已接持久预算、Session多attempt及真实远程文件路径。真实SIGKILL、终态重启和确认丢失等证据见下面新批次；尚缺的场景不能由Go账本重开或Fake测试替代。V2主链路未启动。

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

## 接口边界复查

- `phase9-file-protocol-tests.txt` / `phase9-file-protocol-final-tests.txt`：缺参数、null、无效UTF-8与显式空字符串区分；空文件合法，显式limit=0拒绝。
- helper的存储执行错误退出3→HTTP503，带sandboxRecovery及outcomeUncertain；CAS/路径拒绝仍退出2→403，不混报。分类测试见 `TestStorageFailureIsNotPolicyDenial`，并非真实磁盘故障注入。
- `phase9-go-http-protocol-component.json`和`phase9-go-http-storage-errors-component.json`分别对应runtime-v1.1/v1.2的真实正常链路复验，旧镜像/报告保留；文件名表示实现版本，**没有在这两轮实际注入fsync故障**。
- 生命周期门采用可响应context的串行等待，排队等待不会因普通Mutex而无视取消；测试与最终Go回归见phase9-protocol-cancellation-tests.txt及phase9-protocol-final-regression-tests.txt。
- 原手动挂载夹具没有Go归属账本，显式排除生产预热池且不启用writer finalizer；它不是fencing/自动恢复验收入口。完整Go路径仍强制终止确认协议。

## 下一步

完成V1-04/06/10/11/14，以及显式旧工作区迁移、统一入口和性能统计。三类回放与当前回归已过，后续改动继续相关验证。全部V1完成后再启动V2，保留完整范围，不使用已消费的模型预算。

## 2026-10-18 TaskStore/Runner接线与真实Agent验收

- 部署配置默认nfs与原生持久控制目录；TaskStore启动扫描、同Task多attempt、调用预留、Provider/Replay指纹与游标、原阶段时钟、取消、命令消费游标、独立attempt Trace已接入。local显式保留旧离线后端，旧业务目录不删除。
- Runner定向48/48、TaskStore/API/Session定向32/32；完整Python批次155/155、276.596秒。随后新增配置/路由/预算与runtime数字ID覆盖为68/68、2.822秒；最终HTTP409/Trace鉴权与ID为15/15、0.797秒。相互有重叠，不相加；155项不是后续全部新增用例的最终完整重跑。
- 最后取消竞态修正由`phase9-durable-cancel-final-tests.txt`覆盖，TaskStore/API 9/9、1.151秒；确认blocked owner取消后即使内存中留有已完成句柄，也会对账清理且不新建attempt。该项仍是Fake，V1-06真实并发验收未勾选。
- V1-01：`phase9-agent-workspace-smoke-v2.json`通过，16.488秒。Replay Agent实际经Go HTTP在gVisor非root65532专属NFSv4.1目录写读report.md，内容与服务端hash一致；正常释放Pod后文件保留。
- V1-01首轮失败：Go生成的十六进制sandbox_id以数字开头，被Python复用了“逻辑ID以字母开头”的规则。`phase9-agent-workspace-smoke-tests.txt`保留原失败；修正独立runtime ID规则后，用原Go/Python账本正常停止旧Pod，`phase9-agent-workspace-id-reconciliation.json`记录before/after与停止证明，不强删或抹掉失败数据。
- V1-02首轮：Task已自动两attempt成功，但即时检查API对象消失过早，严格夹具失败。原文`phase9-agent-pod-recovery-tests.txt`及事后账本检查`phase9-agent-pod-first-inspection.json`保留，后者strictVerifierPassed=false，不代替复验。
- V1-02严格复验：`phase9-agent-pod-recovery-v2.json`通过，19.886秒。完整工具组与Session边界持久化后，由管理员正常delete单个测试Pod，不force、不清空finalizer；TaskStore自动停止旧owner、生成新attempt并重新认领同工作区，最终读回同hash。
- 该轮Task/session/branch/workspace保持，2个Pod UID不同、generation=2，原模型槽4/工具槽3保留，旧/新owner均确认停止，最终自然删除观察等待258.69ms。只验证此单节点、正常删除的故障注入，不外推节点网络分区或生产RTO。所有外部模型调用0。

| V1项目 | 当前正式状态 |
|---|---|
| 01 Agent文件写读与NFS hash | 通过，真实本地服务+Replay Agent |
| 02 完整轮次后Pod故障自动接管 | 通过，管理员正常删除的可控故障 |
| 03 强杀agentd自动接续 | 通过，真实SIGKILL -9与同控制目录重启 |
| 04 原预算/截止时间 | 纯逻辑/Fake覆盖，待真实验收 |
| 05 终态不复活 | 通过，四类真实终态及服务重启、三份日志未变化 |
| 06 取消/重复恢复/单写入者 | 部分组件/Fake覆盖，待完整验收 |
| 07 不明文件写入 | 通过，实际写入后夹具客户端丢弃确认；不是TCP断包 |
| 08 模型/工具中断 | 通过，实际Pod工具中断+SIGKILL在途模型，原始父链/槽位审计 |
| 09 旧节点拒绝且历史可读 | 通过，实际agentd分支409、旧路径200、head不变 |
| 10 文件与跨工作区越权 | Go/组件覆盖，待完整验收 |
| 11 NFS不可用/旧writer未确认 | 部分逻辑覆盖，待真实可控实验 |
| 12 释放/取消/失败清理 | 通过，四类真实释放、私有目录/Retain PV/PVC/控制日志仍在 |
| 13 既有回归与三类联合回放 | 通过，165项回归及三类真实存储+脚本/Fake回放3/3 |
| 14 命令游标与多attempt Trace | Fake覆盖，待真实进程验收 |

## 2026-10-18 真实进程、终态、确认丢失与既有回归

- `phase9-agent-process-recovery.json`：真实Uvicorn agentd进程先写文件，在第二个模型调用预留后等待固定Replay响应；管理夹具SIGKILL自身子进程，OS返回-9。第二进程用同控制目录恢复，无手动resume，原Task/session/branch/workspace/deadline/limits/timing保持；旧模型预留转uncertain，合计4个模型槽、一次写入。
- 同轮核对旧/新Pod UID不同、不可变imageId相同、实际gVisor/NFSv4.1及NFS文件hash、原始Session字节前缀保留、当前完整父链Tool配对、Go旧owner释放证明。旧完整节点路径HTTP200、携带工作区分支HTTP409，head不变，覆盖V1-03/09。
- 单轮kill→API ready54.688秒、→替换Pod观察56.925秒、→Task成功65.497秒；包含WSL挂载盘源码启动及8秒Replay等待，不能作为生产RTO/P50/P95。Replay新增可选delayMs仅模拟在途响应，范围0～60000，Live Provider不经过它。
- `phase9-agent-terminal-restart.json`：实际API任务分别产生succeeded、failed、limit_exceeded和运行中cancelled；失败来自刻意损坏Replay参数，超限来自6轮真实list工具循环，取消在运行中调用取消API。第五个服务启动后四类状态与全部Task/Session/Go日志保持，覆盖V1-05；不手工写终态。
- `phase9-agent-write-ack-recovery.json`：真实Go文件RPC成功后，管理夹具客户端丢弃确认并抛出超时；运行时保留写入槽为uncertain，第二attempt实际读hash后继续。只执行/预留write_file一次、可见父链不含旧半组写入，最终hash一致，23.524秒单轮，覆盖V1-07。此注入在客户端确认边界，不能描述成TCP网络断包。
- `phase9-agent-interruption-cleanup-audit.json`：对真实V1-02、SIGKILL与终态报告关联原始日志逐项复查；旧半组读工具AI节点留在审计、不进入当前完整父链，模型与工具槽保留；正常/取消/失败/超限工作区目录65532:65532:700、Retain PV/PVC和Session仍在、对应Pod已消失。覆盖V1-08/12，只读审计未再次执行工具。
- `phase9-joint-ops-replay.json`：实际Milvus2.5.10/OpenSearch和缓存E5/BGE，memory/Service/readiness三类3/3。模型/Jev/沙箱生命周期仍为脚本/Fake，不评自然语言能力或语义引用蕴含；设置HF/Transformers离线及OMP=2，不下载模型、不重建索引。四个项目服务经资源检查临时启动后停回初始状态，ES及其他项目未启动，覆盖V1-13回放部分。
- 完整Python165/165、290.536秒，`phase9-python-process-regression-tests.txt`；Replay延迟/只读监测与恢复定向16/16、0.995秒，`phase9-process-replay-tests.txt`。两份覆盖重叠，不相加。所有外部模型调用0。
- 尚缺04/06/10/11/14真实验收、显式旧工作区迁移、统一入口、小负载性能及全部V2。SIGKILL旧Trace为控制面补录并注明usageKnown=false，后续14仍需检查命令窗口与崩溃证据可追溯，不能把未知用量当已知零。

## 2026-10-18 分阶段时钟与Session多attempt

- 新账本schema2保存首次claim/loop时间锚点：190秒认领与120秒循环保持独立；排队未启动无claim截止时间，进入loop只转换一次，后续恢复认领/停机时间计入原阶段。旧schema1显式deadline原样读取，不额外发放额度。
- 模型/工具调用要求当前attempt已绑定且loop时间已保存；取消、过期和时钟早于attempt开始时停止。资源清理可以在超时后执行，records()不丢弃这类owner。Runner独立清理窗口和实际预算注入仍待接线。
- Kubernetes Pod UID允许数字开头；已有attempt的开始时间、绑定及已确认停止事实不允许回滚。无费用定向测试包含真实UID形状，不表示已运行新Python Pod恢复链。
- SessionJournal.for_attempt共用文件和锁；消息增量按task+attempt组织，半组工具调用留在旧attempt审计，新的父链只继承完整head。重复边界幂等，旧attempt不能继续写，旧线性快照的迁移节点仍归legacy，历史字节保持前缀不变。
- `phase9-attempt-clock-final-tests.txt`为50/50、1.537秒；不等同强杀agentd、真实Pod故障或V1历史节点HTTP限制验收。默认TaskStore/Runner未接这些新接口，正式矩阵保持未完成。
- `phase9-python-attempt-clock-regression-tests.txt`完整批次为137/137、270.335秒；该进程启动后追加的时钟回拨、新绑定前禁止调用和legacy线性视图边界，由最终50项定向覆盖。两份用例有重叠，不合计为187项，也不把早期完整批次标成后来全部用例的完整重跑。
