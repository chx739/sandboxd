# Phase 9 工作区恢复实施记录

日期：2026-10-17起。基线：281111d；V1分支：codex/workspace-nfs-v1。当前M3，完整目标active，未完成；以下按批次保留历史过程。

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

文档43当前正式通过 **V1-01/02/03/04/05/06/07/08/09/10/11/12/13，共13/14**；V1-14及全部V2尚未完成。机器可读证据清单见`phase9-v1-acceptance-progress.json`。纯逻辑/Fake证据不能提前勾选真实集成项目。

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

完成V1-14，以及显式旧工作区迁移、统一入口和性能统计。三类回放与当前回归已过，后续改动继续相关验证。全部V1完成后再启动V2，保留完整范围，不使用已消费的模型预算。

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
- 尚缺V1-14真实验收、显式旧工作区迁移、统一入口、小负载性能及全部V2。SIGKILL旧Trace为控制面补录并注明usageKnown=false，后续14仍需检查命令窗口与崩溃证据可追溯，不能把未知用量当已知零。

## 2026-10-18 分阶段时钟与Session多attempt

- 新账本schema2保存首次claim/loop时间锚点：190秒认领与120秒循环保持独立；排队未启动无claim截止时间，进入loop只转换一次，后续恢复认领/停机时间计入原阶段。旧schema1显式deadline原样读取，不额外发放额度。
- 模型/工具调用要求当前attempt已绑定且loop时间已保存；取消、过期和时钟早于attempt开始时停止。资源清理可以在超时后执行，records()不丢弃这类owner。Runner独立清理窗口和实际预算注入仍待接线。
- Kubernetes Pod UID允许数字开头；已有attempt的开始时间、绑定及已确认停止事实不允许回滚。无费用定向测试包含真实UID形状，不表示已运行新Python Pod恢复链。
- SessionJournal.for_attempt共用文件和锁；消息增量按task+attempt组织，半组工具调用留在旧attempt审计，新的父链只继承完整head。重复边界幂等，旧attempt不能继续写，旧线性快照的迁移节点仍归legacy，历史字节保持前缀不变。
- `phase9-attempt-clock-final-tests.txt`为50/50、1.537秒；不等同强杀agentd、真实Pod故障或V1历史节点HTTP限制验收。默认TaskStore/Runner未接这些新接口，正式矩阵保持未完成。
- `phase9-python-attempt-clock-regression-tests.txt`完整批次为137/137、270.335秒；该进程启动后追加的时钟回拨、新绑定前禁止调用和legacy线性视图边界，由最终50项定向覆盖。两份用例有重叠，不合计为187项，也不把早期完整批次标成后来全部用例的完整重跑。

## 2026-10-18 原预算/时钟与真实文件边界

**V1-04通过，两条实际进程路径：**

- `phase9-agent-budget-restart.json`与同名`-tests.txt`：Task `task-628bd5625227f11c`，完成5个真实NFS目录工具轮次，第6次模型预留后SIGKILL。重启继承原6个模型槽、5个工具槽、deadline/timing/limits/provider，最终limit_exceeded，错误为original model call budget is exhausted；仍在原deadline之前，无新调用或Pod认领。第二attempt仅执行预算判断，never-bound停止。
- `phase9-agent-expired-restart.json`与同名`-tests.txt`：Task `task-81ed0291dc01fd15`，第2模型预留后SIGKILL，按真实墙钟等待原loop截止。原deadline为UTC18:19:49.267215，重启请求UTC18:19:49.468755；第二服务清理原owner后limit_exceeded，仍仅1个attempt、2个模型槽/1个工具槽，错误为original task deadline has expired。没有缩短时钟、写入伪造状态或手动resume。
- 两轮均核对Session原字节前缀、旧预留转uncertain、Go原代次/stopConfirmed及原身份；均是本地真实服务+固定Replay，外部模型调用0。

**V1-10第四轮通过，前三轮保留：**

1. `phase9-workspace-file-boundary.json`首轮发现write_file把新symlink误判为不存在并替换，未通过。修复只允许明确ENOENT创建，不以目录枚举“没看到”证明不存在。
2. `phase9-workspace-file-boundary-v2.json`确认叶子写入拒绝，但父目录symlink读取成功，未通过。额外管理员诊断认领的健康检查失败后，使用原Go账本正常清理，见`phase9-symlink-probe-cleanup.json`。`phase9-symlink-runtime-probe.json`的空observations来自该认领失败，不是验证通过。
3. `phase9-workspace-file-boundary-v3.json`采用openat2候选，正常文件创建即失败。`phase9-openat2-write-probe.json`补实际403；`phase9-path-syscall-probe.json`及源代码`.go.txt`确证当前gVisor的openat2返回ENOSYS，而O_PATH|O_NOFOLLOW能取得链接本身。所有额外诊断Pod均有Go正常释放证明，不使用force。
4. `phase9-workspace-file-boundary-v4.json`与同名`-tests.txt`，runtime-v1.5最终通过。标准库目录描述符取得→fstat类型确认→基于该描述符打开“.”，不重新解析原路径，不添加依赖/权限。真实Go HTTP绕过Python Policy，20项路径/归属/额外参数/大小/叶子及父级链接/CAS拒绝，正确CAS写入对照成功；两个guard hash及NFS服务端hash一致，链接仍为原链接，两个owner停止确认。suffix v2/v3/v4仅表示重跑次数，均属NFS V1，不是restic V2。

固定Docker镜像runtime-v1.5的inspect Id为`sha256:8e1abdb6e17c11c98fb04f7ad707d748a35c199c9fc80fabbec300a60c7fb465`；真实Pod CRI imageID为`sha256:ee4b19828a03a59cb10941cd1aa03e8829e47c82466e3f1977bfd001b5fdd1d1`，不把两者混称registry manifest digest。旧镜像、工作区、PV/PVC、原始日志全部保留。该结果不宣称多租户认证或网络分区HA。

**回归：** 最终`phase9-directory-descriptor-tests.txt`相关Go race通过、`phase9-go-directory-descriptor-regression-tests.txt`全Go回归通过；含目录路径被替换后仍使用原目录句柄及fsync。`phase9-budget-boundary-final-regression-tests.txt`22/22、0.699秒，覆盖夹具/原阶段时钟/Replay监测。此前165全Python结果仍是前一批次，没有冒称本次全量重跑；各批次重叠不相加。openat2候选和ENOTDIR断言的失败输出保留。

当前正式13/14；剩余V1-14、旧文件迁移、统一入口、固定负载性能及全部V2仍待完成，Goal active。

- 最新runtime-v1.5真实Agent文件写读及完整边界Pod故障接管复验通过：`phase9-agent-pod-descriptor-recovery.json`，23.491742秒单轮，原Task/工作区、2个attempt/不同Pod UID、模型4槽/工具3槽、服务端hash保持。该项是路径修复后的集成复查，不是额外生产RTO或模型能力成绩。

## 2026-10-18 并发取消与NFS故障

**V1-06第二轮通过：** `phase9-agent-concurrency-v2.json`及同名`-tests.txt`。

- 实际第二个Uvicorn agentd使用同一控制目录、不同18091端口，因JournalInUse退出码1结束；日志不含端口占用原因，任务日志字节未变，第一进程继续ready。这是实际服务启动拒绝，不是同进程Mock锁。
- 强杀自己的第一agentd后，同目录自动接续原Task。旧attempt release返回409，新owner/Pod UID/代次保持且直接读回原guard hash；两个并发手动resume均409，没有新增Task；两个并发cancel均202，最终cancelled，所有attempt有停止证明。
- 第二Task完整轮次后正常删除自己的Pod，不force。替换attempt的Go phase=starting时发cancel，取消持久化时间UTC18:55:39.513803；后续没有该attempt的bound记录，也没有第3次模型调用，两个attempt停止、最终cancelled。仍核对原scope/limits/deadline/timing，不用绑定后取消冒充starting竞态。
- 首轮`phase9-agent-concurrency.json`失败：重复cancel再次打断shield清理，公开状态interrupted。夹具退出服务后用原Go账本正常对账，没有抹掉失败。Fake固定释放窗口的修复前1→2次取消证据见`phase9-duplicate-cancel-before-fix-tests.txt`；只对cancelling幂等，interrupted仍能重排队核查，16项定向通过`phase9-duplicate-cancel-final-tests.txt`。

**V1-11第二轮通过：** `phase9-nfs-unavailable-v2.json`及同名`-tests.txt`。

- 开始前两个工作区命名空间没有其他Pod，核对专属NFS项目标签/完整容器ID。只暂停该NFS容器30.216秒，使用新随机名的未缓存文件路径；Go文件RPC30.017秒返回503、sandboxRecovery=true，非策略403或成功假数据。
- NFS仍暂停时，Go owner没有stopConfirmed，新attempt claim409，工作区代次/归属未变；不把文件超时或Pod删除请求等同于停止证明。
- 恢复NFS后先Go Stop确认旧容器终止，再gen2创建不同UID的真实gVisor/NFS Pod，读回服务端相同SHA256：`323302d94d91adbc4ca4bfdcf52171ea62a8643c65de40196a39047b18ba5edb`；最后正常释放，数据保留。
- 45秒固定容器ID的一次性看门狗在故障前启动，正常流程主动unpause后结束它；没有宣称实测父进程强杀下看门狗。首轮`phase9-nfs-unavailable.json`已观察503/409但错误复用Exec超时请求回收的旧Pod，后续读取失败，原报告保留且不计正式通过。该场景是Go/gVisor/NFS边界，不冒称完整Python Agent存储故障自动恢复或网络分区HA。

**回归与时钟诊断：** 首轮`phase9-python-concurrency-regression-tests.txt`完整175项有1项shutdown时钟失败；20次`phase9-shutdown-clock-reproduction-tests.txt`再次1次失败。`phase9-host-clock-observation.json`独立30秒2942样本确认2次墙钟倒退约1.242/0.813秒，单调时钟正常前进；校时来源未定位，不改系统时间。生产回拨保护保留并附observed/started，Fake Store纯逻辑用显式固定时钟；Runner预算预检先于协程创建，新增0认领/无未await协程/never-bound清理测试。最终39项定向通过4.593秒，`phase9-cancel-clock-final-tests.txt`。最终全量另记，不覆盖首轮失败；所有本轮外部模型调用0。

最终完整回归`phase9-python-concurrency-final-regression-tests.txt`：176/176、280.193秒，原175项失败未覆盖。三类联合运维复验`phase9-joint-ops-concurrency-replay.json`：3/3，实际Milvus2.5.10/OpenSearch和缓存E5/BGE，脚本模型/Fake路由/Fake沙箱；HF/Transformers离线、OMP=2，未重建或导入索引。四个已有项目服务临时启动后已停止回原状态，ES未启动。该全量含固定时钟Fake Store测试，不宣称宿主墙钟问题已修复；真实SIGKILL/原截止时间证据仍单列。

## 2026-10-18 命令游标与崩溃前调用审计

本批次增加 `call.audit`：模型响应的计数/已知标记和工具的有界脱敏证据在调用完成确认前 fsync。调用状态与审计独立，中断后没有响应的预留仍占用额度、用量未知；已有响应的 token 和证据可从旧 attempt 查询，不能统一写零。新增格式读取旧日志，旧写入器不支持新记录，不支持把新控制目录直接交给旧版本回退。

首次真实通过 `phase9-agent-command-recovery.json`：HTTP 接收 steer/follow-up 后，实际 SIGKILL 自建 agentd，再同目录自动接续；同 Task/分支/工作区/预算/原时钟保持。两条命令各注入一次、只写一次 guard，旧 Trace 可查原写入步骤与证据 ID。旧 attempt 有2次模型预留但只保存1次响应，因此 `usageKnown=false`；记录的 Replay 零用量是无外部模型，不冒称 SDK 账单。第三次终态服务重启后任务/会话/Go日志字节不变。实际 Go/gVisor/NFS，脚本模型，不是生产 HA。

`phase9-agent-audited-write-inspection.json`：实际写入后客户端丢弃确认，再读取目标 hash，`workspace-recovery` 核查和模型读取都进入后续 Trace；写操作仍仅一次。不将客户端丢确认描述为 TCP 丢包。

另补两个持久化窗口的确定性测试：Session fsync 后、命令消费游标前中断，恢复补游标不重注入；ownerStopped fsync 后、最终 Trace 前中断，恢复先补旧审计再新建 attempt。停止未确认且缺少最终 Trace 时公开查询只给 `partial-audit` 只读视图，查询前后日志不变、不新认领。老 attempt 不引用后续 attempt 的新 head。上述精确窗口由显式 Fake/CancelledError 注入，不冒充真实强杀时序。

新增边界最终定向 `phase9-call-audit-final-boundary-tests.txt`：34/34，5.433秒。早期完整回归 `phase9-python-call-audit-regression-tests.txt`：188/188，277.950秒，发生在最后 ownerStopped/Trace 窗口与只读投影修复前，不能代表最终代码全量。最新真实复验与最终全量结果另列；原始结果不覆盖。

最终代码实际复验 `phase9-agent-command-recovery-final.json` / 同名`-tests.txt`通过：Task `task-ce136e4d18a06604`，两条命令计数 `[1,1]`、实际OS强杀、两attempt/不同Pod、旧工具证据、原预算/时钟、终态第三进程日志不变均通过。夹具退出码0，sandboxd-demo/storage无遗留Pod，NFS Running=true/Paused=false；卷和文件保留。至此V1矩阵14/14有对应证据；这不等于V1全部交付或完整Goal完成，迁移、统一入口、性能和10项V2仍待完成。最终全量回归见下方结果。

最终全量 `phase9-python-call-audit-final-regression-tests.txt`：190/190、271.294秒，通过，包含最终ownerStopped/Trace窗口和只读投影；与此前188批次分开、不相加。compileall和diff检查通过，新文件凭据形状启发式扫描无匹配（不是完整秘密扫描证明）。Goal仍active；下一步显式迁移、统一命令、固定负载性能，再V2独立分支。

## 2026-10-18 显式旧工作区迁移与统一入口

新增`agentd.workspace_migrate`管理CLI、`runtime/workspace_imports.py`与TaskStore首次resume映射。先预览文件manifest/源Session hash，apply持Task账本锁并要求源匹配；新专属NFS目录经原Go接口复制、list_files完整清单hash核查、旧owner停止确认、Session副本fsync后，imports.jsonl才COMMITTED。未完成记录保留；重试先停止旧owner、相同hash跳过，最多3次导入attempt。只支持现有文件工具可完整校验的UTF-8文本范围；100文件/16MiB/单文件256KiB，空目录、symlink、硬链接、特殊文件拒绝。不是通用POSIX元数据迁移或V2历史快照。

真实两轮报告：`phase9-agent-workspace-migration.json`、`phase9-agent-workspace-migration-entry.json`。两者都是合成旧线性Session+两个文本文件，包含中文路径；实际Go/gVisor/NFS，客户端丢弃首个写确认，再执行迁移。两个导入attempt、两个文件各实际写一次，第二次按hash跳过已写文件；重复apply不改imports日志。COMMITTED后手动resume产生新Task，Replay只读文件并核对工具返回与NFS服务端hash；V1旧节点携带工作区恢复拒绝，源Session/源文件保持。该项不是实际用户数据迁移，也不是TCP丢包或外部模型质量测评。外部模型0。

第二报告原字节复制自`.cache/workspace-eval/run-LV9TMnKz/migration.json`，SHA256=`318015ad9318290cb12651b14c21ae5bf2c3ce1f739fe34653109fc7a388fd7e`；命令是`bash hack/workspace.sh verify migration`。入口只读check原文`phase9-workspace-entry-check.txt`、分发原文`phase9-workspace-entry-migration-tests.txt`均通过。统一入口还提供install/build/nfs/go/agent/test/migrate/verify all/joint；本批次未逐一重新安装/启动或执行all/joint，不把分发代码视为这些运行证据。输出每次新建目录，失败原文与控制目录保留。

首次`phase9-migration-initial-tests.txt`：19项有3错误，使用了不符合Go实际响应的sha256字段；修正为newSha256后`phase9-migration-boundary-tests.txt`19/19、3.534秒。最终完整`phase9-python-migration-regression-tests.txt`197/197、288.024秒，通过，含迁移幂等/未知写核查/线性副本/路径与映射边界、既有恢复。首次失败不覆盖，各测试重叠不相加。compileall、bash语法、diff检查通过。没有修改本机时间、系统权限或现有数据卷。

当前V1矩阵14/14和显式迁移有证据，完整启动分发验证与固定负载性能仍待完成，V2未开始；Goal active。下一步先复验统一入口的Go/agentd启动与接续，再测同条件文件工具/挂载/恢复/资源与流量，完成V1交付后另开V2依赖分支。

## 2026-10-18 V1统一启动与固定负载收尾

完整Goal仍active，V2尚未开始。本批次补齐V1剩余交付项：

- `phase9-workspace-entry-install-tests.txt`：实际缓存重复安装，固定Go1.26.5/kind0.31.0/runsc20260810校验和通过；uv frozen all-extras检查79包未变化。原安装入口不带extras会移除现有RAG包，已修正。首次联网安装未重复，不虚称重新下载所有依赖。
- `phase9-workspace-entry-build-tests.txt`：统一Go构建成功；`phase9-workspace-entry-nfs-tests.txt`/`phase9-workspace-entry-nfs-reuse.json`：no-recreate启动复用与V1-11相同容器497b2542…、原IP172.23.0.3和named volumes。没有扩大权限或重建业务镜像。
- `phase9-agent-unified-entry-final.json`及原始输出：实际通过bash entry→go/agent启动，Python环境未包含Operator Token，SIGKILL后同Task接续，原scope/limits/timing/provider不变、写一次、读回NFS服务端hash。单次强杀→API57.07秒→新Pod观察59.65秒→读回60.04秒→成功68.31秒；包含挂载盘源码导入、轮询和8秒Replay等待，不当生产RTO。首次入口样本另存，不覆盖。
- `phase9-agent-pod-recovery-timing.json`原字节复制自`.cache/workspace-eval/run-7qsr3gDy/pod.json`：请求正常删除自己的Pod→失败文件RPC112.74ms→旧owner停止2245.30ms→新claim就绪3494.61ms→成功读回3839.60ms。写预留1、读预留2（含失败）、新attempt成功检查1；只加管理观测，不改变Actor Loop或Go策略。
- `phase9-nfs-fixed-workload-serial.json`：同机单并发，固定seed20261018，4×1KiB、32×256B、8×4KiB/改1文件，3轮9工作区、267计时读写，9/9通过且所有hash/清单正确。NFS窗口收发3,462,282字节（3.30MiB），含mount/元数据/协议，非公网；工具P50约199–201ms，不能当纯NFS延迟/QPS上限。GoCPU1.95秒、NFS cgroup CPU0.72秒、控制器CPU0.41秒；端点内存非峰值。正式基线时没有并发项目测试/安装，数据库服务已停回原状；宿主其他后台未完全隔离。首次并行探索`phase9-nfs-fixed-workload.json`单列保留，不混算。
- `phase9-entry-joint-replay.json`3/3：统一joint入口，实际Milvus2.5.10/OpenSearch/缓存E5-BGE；决策/路由/沙箱生命周期仍为脚本/Fake。4个已有服务临时启动、结束停回原状态，未导入/重建索引，ES未启动；服务白名单与最终状态`phase9-entry-joint-services.json`。外部模型0。
- `phase9-python-benchmark-final-regression-tests.txt`：最终201/201、288.476秒，通过；此前200/280.764秒与负载夹具3项原文保留，不累加。`phase9-workspace-entry-guards.json`为实际shell拒绝live/未知case，未启动模型。compileall、shell语法与diff检查另核对。

V1的14项验收、显式迁移、安装启动和小规模基线具备交付证据；完整数值/口径/失败边界见文档46。verify all本批次未整套再跑，其各场景原始证据仍登记在矩阵。文档43的全部10项V2及V1/V2同条件比较尚未实现，不能标完整Goal完成；下一步从V1最新HEAD另开V2依赖分支。

验证备注：Docker Compose原始stdout末尾自带空格，phase9-workspace-entry-nfs-tests.txt保持原字节；不为通过格式检查修改原始证据。排除原始txt日志后源码/文档diff检查通过，所有索引checksum仍一致。
