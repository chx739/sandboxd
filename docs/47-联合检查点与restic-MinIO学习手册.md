# 联合检查点与 restic + MinIO（V2）

> Phase9 M4进行中；V1交付基线9c91c9b，V2分支codex/workspace-restic-v2，[草稿PR#3](https://github.com/chx739/sandboxd/pull/3)依赖V1 PR#2，首批提交90d9c22。本文区分后端组件验证与完整Agent联合检查点；组件通过不能直接勾选V2十项验收。规格以文档43为准。

## 1. 已固定的实现路线

- NFS继续作为业务文件权威位置；不改成本地盘运行、不异步上传。
- 独立MinIO服务/数据卷：`deploy/workspace/backup-compose.yml`，不使用或更改RAG MinIO的对象/卷。固定已有官方镜像RELEASE.2023-03-20T20-16-18Z及实际digest，初版仅kind私网与localhost9002的HTTP；没有宣称验证TLS或生产部署。
- restic固定0.18.1，官方release及版本化文档可核对；压缩包SHA256 `680838f19d67151adba227e1570cdd8af12c19cf1735783ed1ba928bc41f363d`，解压二进制SHA256 `01143daba61a1dc8afb0cb3d03ba83e6b118f55b6b146b27473a09efc3df13d6`。校验官方HTTPS资产和发布表，没有宣称完成GPG签名验证。
- 备份镜像独立于V1业务runtime-v1.5：`sandboxd-restic:0.18.1-v1`。业务镜像、用户旧目录、旧索引/卷保留。

## 2. 可信维护入口

`cmd/sandbox-restic`只接固定操作和标识：manifest/init/config/snapshots/backup/restore/check/stats；backup只接受UUIDv4 checkpoint ID，restore只接受完整snapshot与manifest hash。没有自由Shell、源路径、目标路径、仓库URL、密码或附加argv参数，不暴露为Agent工具。忘记/prune/unlock尚未开放；后续必须经过引用保护与串行管理入口。

restic进程的仓库和密码从只读Secret文件读取，S3凭据用SecretKeyRef环境注入，仅可信维护Pod拥有。命令错误不返回raw stderr，避免S3认证信息进入公共报告；非零退出（含部分备份）一律失败。

维护Pod复用gVisor/非root65532/只读根/drop ALL，去掉API token卷，使用default ServiceAccount但automount=false；源PVC以固定workspace ID作SubPath，备份只读，恢复仅写指定的新空工作区。临时盘64MiB，可信维护进程内存上限512MiB；没有扩大业务Pod权限。存储命名空间原deny policy不变，新增只匹配backup标签、只到专属MinIO IPv4:9000的策略。

文件manifest复用Go描述符遍历、路径/link/大小/CAS相关底层约束，最多512项/64MiB、单文件256KiB。不创建只读挂载上的锁；hash包含用户文件相对路径/大小/mode/内容SHA256。内部`.sandboxd-lock`及`.sandboxd-tmp-*`保留在原目录但不进备份，因其为RPC禁止的内部名、不是已确认用户文件；临时文件仍计入配额。空目录与其他元数据由restic保存，当前用户文件manifest不自称覆盖全部POSIX元数据。

同一cp tag的后端请求如已存在唯一快照且manifest tag相同，可返回同一个完整snapshot ID；不同manifest或多候选拒绝。这个**后端幂等**不等于联合检查点COMMITTED。恢复先确认目标无用户项、完整ID/manifest tag匹配，使用snapshotID:/workspace恢复并verify，再核对文件manifest；不覆盖原目录、不使用latest。

## 3. 下一层接线必须满足的边界

1. **持久写屏障**：不能只拿一次内存mutex跨备份。开始前将checkpoint/job/owner/代次写入Go持久账本；崩溃重启后Files/Claim仍阻止写入，直到可信维护Pod停止已确认。未知Pod状态、NotFound或租约到期都不能单独释放屏障。
2. **边界与引用**：完整Session节点已fsync，保留可读取的父链及校验信息；会话/记忆/控制数据在沙箱外。半组工具消息不创建检查点。
3. **提交协议**：UUIDv4首次分配持久化，PREPARING→DATA_READY→COMMITTED；COMMITTED与分支头/revision前移用一条可恢复日志表达。提交前核对原owner/代次/分支头/未取消；客户端确认丢失通过相同ID查询，不复制一次保存。
4. **取消与时钟**：备份等待计入原Task阶段时间，不补预算。扫描/上传期间允许取消被记录并终止维护任务；最终提交检查与Task取消的本地接受顺序须串行，不能持全程备份锁让取消等待90秒。未知备份结果保留孤立快照，不自动forget。
5. **恢复模式**：NFS最新目录可用则继续V1模式，不用旧快照覆盖；显式历史恢复/分支写新空workspace，匹配同一会话节点/镜像身份。已有Task预算/截止时间/调用审计不可回退，自动接续不能用新Task伪装预算重置。
6. **引用保护**：保留分支头、明确保留项与活跃恢复源保护snapshot；默认无自动forget/prune，管理预览先检查所有引用，备份与清理串行，不强行unlock或删除MinIO底层块。

本节列出完整不变量。后端及控制模块到Agent正常保存的接线已逐步实现，证据见下文；恢复、真实故障和引用清理仍待完成。

## 4. 组件验证入口与当前范围

```bash
bash hack/install-restic.sh
# 前提：已完成V1部署，Go1.26.5、kind、gVisor/NFS、Python frozen环境可用。
export PATH="$HOME/.local/bin:$PATH"
export GOMODCACHE=/tmp/sandboxd-workspace-go-mod
export GOCACHE=/tmp/sandboxd-workspace-go-build
mkdir -p .cache/restic-image
CGO_ENABLED=0 go build -o .cache/restic-image/sandbox-restic ./cmd/sandbox-restic
cp "$HOME/.local/share/sandboxd-tools/restic-0.18.1/restic" .cache/restic-image/restic
docker build -f deploy/workspace/restic.Dockerfile -t sandboxd-restic:0.18.1-v1 .cache/restic-image
kind load docker-image --name sandboxd sandboxd-restic:0.18.1-v1
go build -o .cache/workspace-backup-pod ./hack/workspace-backup-pod
bash hack/workspace.sh build
agentd/.venv/bin/python -m agentd.workspace_backup_component --output .cache/restic-component.json
```

组件创建本次合成工作区，经真实Go文件RPC写入后由Go停止业务owner，保证探测时没有业务写入；它**尚未验证Actor运行时的持久屏障**。实际gVisor维护Pod备份到独立MinIO，重复请求核对snapshot复用，合成文件增/改/受控删除后再备份，向两个新空NFS目录分别恢复旧/新版本并从业务Pod核对hash。只有本次生成且hash核查过的`remove-synthetic.txt`被删除；不触碰用户旧目录。凭据在原生Linux私有目录，所有快照/卷/控制日志保留。

实际报告：`evidence/phase9-v2-backup-component-v3.json`整体通过，8次维护操作均为`io.containerd.runsc.v1`、UID65532。两个快照完整ID：

- 第一版：`3b802932b964ff192b69bde8eb862cbc2230b104f305683ca2a933c1578b21eb`。
- 第二版：`2f20bc5c487d751a276ea4022f4f54d6bbec8fa83180cef2dad401068c8848aa`。

镜像身份区分：Docker构建manifest ID为`sha256:b21be9a50f56a9d9bce719ecd1a57ef052708d1da919b8fb5db6bfca7a536a35`，运行容器实际imageID为`sha256:d84a466326ed959920fee751772ddfed5bfd0e766a4fa76c552d5063f0911b64`，没有将两者混称registry digest。仓库统计2 snapshots / total_size 1384字节，是极小合成夹具，不能当性能或去重收益。

首轮失败缺NetworkPolicy TypeMeta；第二轮遇Kubernetes省略readOnly=false字段，夹具取字段失败。原始失败报告`phase9-v2-backup-component.json`、`-v2.json`保留，修复后才完成第三轮，不改写失败成绩。

当前只运行kind控制节点、NFS和专属备份MinIO；组件业务/维护Pod已清理，所有工作区、快照和Secret保留。本节属于早期组件证据；后续Session联合提交正常路径见§7。崩溃/分支恢复及引用GC尚未验收，不能宣称V2完成。全部十项正式验收仍pending，机器可读范围及证据hash见`evidence/phase9-v2-progress.json`。

### 持久屏障模块与可信备份执行器

`internal/sandbox/workspace_barrier.go`与owner共用Go独占、fsync的JSONL。预留屏障与文件RPC使用同一短锁，先等待已在执行的RPC返回；随后长期扫描/上传不占这把锁。状态为reserved→submitted→identified→stopped→released，记录checkpoint/job/owner/代次和维护Pod UID。Files和Claim在屏障未释放时均拒绝；取消仍可停止业务owner，但无法绕过屏障换新Pod。

提交API请求前先记submitted；请求结果不明或Pod消失不等于从未提交。只有从未提交的reserved，或可信Runtime确认同UID容器终止后，才能进入stopped，再解除。旧状态的精确CAS不能解除新job。注册表重开时同时重放屏障，非法状态迁移即使JSONL校验和合法也拒绝启动。

8项定向测试通过；全项目Go测试通过`phase9-v2-go-full-regression-tests.txt`，六包race回归见`phase9-v2-barrier-regression-tests.txt`。该批为逻辑证据；后续真实执行器探测见下节，后续Agent接线见§7。V1旧记录保持可读；写过V2屏障的控制目录不支持旧二进制继续写入，旧reader拒绝未知事件。

### 后续真实执行器探测

`internal/sandbox/backup_runtime.go`提供可信的Start / Wait / Abort / Finish，不注册Agent工具，不开放任意Pod spec。Go在屏障中固定维护镜像、仓库Secret名称和MinIO地址；启动重试不能换仓库配置。维护Pod带专属finalizer，读取时检查名称/UID、checkpoint注解、安全基线、挂载和固定argv。容器终止、节点Ready及近期Lease验证后先将停止证明fsync；Wait仅返回有界、结构合法的完整snapshot/repository/manifest身份，非零退出或坏结果不返回成功。仓库维护在MVP中全局串行，旧未解屏障会阻止新维护任务。

Wait完成后仍保持屏障；上层写入COMMITTED或持久失败结果后才可Finish。Abort使用UID条件正常删除并等待真实终止，不强删finalizer、不unlock、不删除孤立快照。ctx超时本身不解除屏障。Finish确认丢失可用原请求幂等重试。没有维护Pod停止证明的NotFound仍拒绝；有已落盘停止证明后才允许清理重试接受NotFound。

首轮`phase9-v2-executor-component.json`整体失败：已证实注册表重开后拦截成功，Pod实际完成备份，但即时结果读取返回通用不可用错误。对同一job再次读取并完成对账成功，`phase9-v2-executor-postfailure.json`保留；没有重做备份或改写首轮成绩。首轮未细分失败阶段，不能据此认定唯一根因；后续增加静态阶段错误，并为容器先终止、Pod终态稍后更新的情况增加原deadline内等待与单测。

第二轮整体成功，但清理后CRI实例已回收，事后runtime观察报告记录0匹配，不用RuntimeClass代替实际runsc证明。第三轮`phase9-v2-executor-component-v3.json`在Finish前读取CRI白名单字段，实际runtimeType为`io.containerd.runsc.v1`，整体成功：

- 新工作区写入44字节合成报告；备份期间读/写RPC和认领被屏障拦截。
- 关闭并重新打开同一Go JSONL，原owner/工作区不变，屏障仍生效。这不是进程SIGKILL实验。
- restic实际备份成功；停止证明落盘后仍冻结，显式Finish后内容/hash不变，禁止写入未留下文件。
- 业务owner经Go确认停止，维护Pod正常清理，所有NFS文件、snapshot、Secret与控制日志保留。

执行器7项顶层测试（另含子用例）覆盖创建结果不明不重发、配置/UID变化、超时、UID条件取消、非零退出、坏JSON/超大输出、节点Lease、Pod终态滞后及重复Finish；与既有屏障8项有重叠，不能相加作验收通过数。六包race见`phase9-v2-executor-final-regression-tests.txt`；Go全项目见`phase9-v2-executor-go-full-tests.txt`；随后Finish幂等修复的sandbox/API race见`phase9-v2-executor-finish-regression-tests.txt`。Python应用未改，本批未重跑Python全量。

最小管理探测（先运行上一节初始化专用仓库的组件，并从其报告取Secret**名称**；不要打印Secret值）：

```bash
go build -o .cache/workspace-backup-runtime-smoke ./hack/workspace-backup-runtime-smoke
# SANDBOXD_BACKUP_PROBE_DIR必须是新的原生Linux私有控制目录。
.cache/workspace-backup-runtime-smoke -nfs-server "$SANDBOXD_NFS_SERVER" \
  -minio-ip "$SANDBOXD_BACKUP_MINIO_IP" -secret-name "$SANDBOXD_BACKUP_SECRET_NAME" \
  -control-dir "$SANDBOXD_BACKUP_PROBE_DIR" -output .cache/backup-runtime-probe.json
```

本节为可信Go执行器阶段的历史证据。随后已接HTTP、Session引用、提交日志和Agent正常路径（§5–7）；restore写屏障及完整验收仍待完成。不能把组件本地backend-result文件当成联合检查点提交。V2十项完整验收与性能比较均保持pending。

## 5. 联合检查点控制模块（本节记录接线前的逻辑验证）

新增`runtime/checkpoints.py`、`runtime/session_refs.py`与`SessionJournal.checkpoint_prefix`。这不是另一套Agent Loop：它们只负责存储和校验，真实维护Pod仍由Go执行；Task/Session/记忆留在沙箱外。

### 提交顺序与数据归属

```text
Go Reserve：fsync屏障，不创建维护Pod
    ↓
Session完整节点 → 导出所选父链 → 不可变Session引用文件fsync
    ↓
Python checkpoints.jsonl：PREPARING（首次UUIDv4、绑定、父检查点）
    ↓
Go Start / Wait：实际restic，停止证明已落盘，屏障仍保持
    ↓
Python DATA_READY：固定仓库逻辑ID/crypto ID/完整snapshot ID/manifest
    ↓
协调器核对Go owner、job和Task取消/截止时间
    ↓
RecoveryLedger.commit_checkpoint：在与cancel相同的短锁内提交
    ↓
一条COMMITTED日志同时表达checkpoint成立、branch head前移、revision+1
    ↓
Go Finish解除屏障
```

上述顺序已由§7协调器接入真实Agent正常路径。Go Reserve只冻结、不运行restic；Session引用/PREPARING落盘后才Start。真实正常保存证据不能替代崩溃/历史恢复及十项完整验收。

### Session引用为何要复制

只保存nodeId不能证明未来可恢复。`session-refs/<sha256>.json`包含sessionId、所选完整节点、告警和按父链排序的节点消息。父子关系、工具调用配对、节点/字节预算均核对；先写0600临时文件并fsync；单写入者核对目标不存在后原子rename发布，再fsync父目录。目标已存在时校验原件，不覆盖损坏引用。引用保存固定相对路径、内容hash、字节数和作用域；读取拒绝路径逃逸、symlink、硬链接、权限/内容/作用域变化。源会话继续追加或出现兄弟分支，都不会改变旧引用。原JSONL不删改。初稿使用link发布，审查发现崩溃后可能残留第二个硬链接而拒读；已改为rename，发布前中断不暴露不完整对象，rename后/fsync目录前中断可验证完整原件后重试。

该文件上限2MiB、2048节点；超过时明确失败，不偷偷截掉工具消息。它是恢复所需的公开消息副本，不备份Task调用预算、隐藏思维或Secret。引用损坏/丢失时restore_point拒绝，不回退到最新Session或相邻检查点。

### 提交日志与取消顺序

每条状态使用DurableJournal的独占文件锁、校验和、fsync和断尾处理。PREPARING/DATA_READY可查询但不能作为restore_point，FAILED不改变旧head；COMMITTED才投影分支头。分支revision只在成功提交时递增；相同请求确认丢失不追加记录，不按最大UUID或时间猜head。boundary查询可在重启后按task/attempt/node找回原checkpoint ID，避免重新分配。

RecoveryLedger新增同步commit_checkpoint，在与cancel共用的RLock内核对：Task仍运行、未取消/未过期、完整节点一致、当前attempt/Pod UID及Go工作区generation/image一致。该方法不改Task额度、deadline或调用审计。取消先落盘则拒绝提交；提交已成立后再取消，不撤销历史检查点。网络备份和Session复制不能放入这把短锁。协调器仍须从可信Go取得最新owner和有效job/屏障，不能拿模型自报字典替代查询；该网络接线后续已由§7实现。

### 当前验证边界

首次12项检查点单测通过，原文`phase9-v2-checkpoint-initial-tests.txt`；随后完整Python214/214、285.135秒通过`phase9-v2-checkpoint-full-regression-tests.txt`，包含线程取消/提交顺序与兄弟分支断言。后续镜像身份/作用域校验及新增第14个检查点用例由34项定向回归（2.140秒）覆盖，见`phase9-v2-checkpoint-final-boundary-tests.txt`；不把此前214项冒称包含后改动，也不将重叠测试数相加。最后引用发布窗口修复由35项定向回归（2.401秒）覆盖，见`phase9-v2-checkpoint-publication-final-tests.txt`；先前214/34批次不是最后改动的完整重跑。Go Reserve及相关API的race结果见`phase9-v2-checkpoint-reserve-go-tests.txt`。

测试中的仓库/snapshot身份明确为合成，owner字典是固定夹具；不能当真实Go/restic联合证据。HTTP与Python客户端的后续证据见下节。本节记录接线前验证；完整轮次自动保存后续见§7。真实服务中的取消/崩溃对账、restore新目录/历史分支、引用GC及V2十项完整验收仍待完成，Goal保持active。

## 6. 受限备份HTTP与Python客户端

新增Agent Token保护的接口：

- `GET /api/v1/workspaces/{workspace}/backup`：查询已持久化的屏障，含配置引用名称、不含凭据值。
- `POST /api/v1/workspaces/{workspace}/backup/{action}`：action仅reserve/start/wait/abort/finish；请求仅binding、UUIDv4 checkpointId、jobId和最多90秒timeoutMs。

Go独立校验scope和已有job；额外image/secretName/repository/argv字段、shell/unlock/forget/restore动作均拒绝。wait超时保留屏障，abort/finish用独立30秒清理context；不随客户端断连丢失停止确认。这里只接可信备份编排，没有增加LLM工具。

默认关闭，可信Go启动配置须同时提供三项：

```bash
# 仍需既有NFS、业务镜像、原生控制目录、独立Agent/Operator环境凭据。
go build -o .cache/workspace-sandboxd-v2 ./cmd/sandboxd
.cache/workspace-sandboxd-v2 -pool-size 0 \
  -workspace-nfs-server "$SANDBOXD_NFS_SERVER" \
  -workspace-image sandboxd-workspace:runtime-v1.5 \
  -workspace-control-dir "$SANDBOXD_WORKSPACE_CONTROL_DIR" \
  -workspace-backup-image sandboxd-restic:0.18.1-v1 \
  -workspace-backup-secret "$SANDBOXD_BACKUP_SECRET_NAME" \
  -workspace-backup-minio-ip "$SANDBOXD_BACKUP_MINIO_IP"
```

Secret名称来自已初始化的专属restic仓库；S3 URL/密码仍在Secret中，不放HTTP参数。三项配置不齐、非IPv4 endpoint或非gVisor拒绝启动；V1未配置时保持原行为。本节当时只使用相同Server路由的loopback HTTP探测；§7联合夹具随后已用实际Go子进程及相同新flags验证。

Python `SandboxdClient.workspace_backup`只接同样固定动作及绑定，校验ID/有限超时，响应有界，无自动重试；start响应丢失留给协调器查询原job，不另起一次保存。GET状态和这些控制返回值不回灌模型。

真实组件入口（沿用前文已准备的NFS/MinIO/Secret/镜像）：

```bash
go build -o .cache/workspace-backup-runtime-smoke ./hack/workspace-backup-runtime-smoke
.cache/workspace-backup-runtime-smoke -http \
  -nfs-server "$SANDBOXD_NFS_SERVER" -minio-ip "$SANDBOXD_BACKUP_MINIO_IP" \
  -secret-name "$SANDBOXD_BACKUP_SECRET_NAME" \
  -control-dir "$SANDBOXD_BACKUP_PROBE_DIR" -output .cache/backup-http-probe.json
```

`phase9-v2-backup-http-component.json`整体通过：实际loopback HTTP鉴权、Reserve/Start/Wait/Finish，跨注册表重开仍阻止Files/Claim，Wait完成后继续冻结，Finish后内容/hash一致；清理前CRI实际`io.containerd.runsc.v1`，业务owner经Go确认停止。工作区、快照、Secret与日志保留。它不包含Python Agent/Session联合提交或进程SIGKILL，不能勾选正式V2验收。

Go路由/配置/Runtime race见`phase9-v2-backup-http-final-go-tests.txt`，Go全项目见`phase9-v2-backup-http-full-go-tests.txt`。Python客户端/远程文件/检查点26项本地测试通过（1.948秒），`phase9-v2-backup-client-tests.txt`；客户端用MockTransport，真实HTTP组件的请求方是管理Go夹具，两种证据分开。本节当时未接Agent；随后接线及明确剩余边界见§7。

## 7. Agent完整边界自动保存与最终诊断接续

### 调用路径与开关

`main.create_app → TaskStore / AgentRunner → PiStyleAgentLoop.on_boundary → CheckpointCoordinator.save → SandboxdClient → Go可信备份执行器`。不新增模型工具或另一套Agent循环。配置默认`AGENTD_CHECKPOINT_MODE=off`，V1行为保留；`restic`仅允许NFS及持久Task/Session。`AGENTD_CHECKPOINT_REPOSITORY_ID`只接受逻辑ID，默认`repo-workspace`，不接受S3 URL或凭据。

协调器先检查可信Go备份服务是否配置；未启用时在模型调用和claim之前失败，不产生额外模型用量。按§5顺序冻结、复制Session、保存PREPARING、Start/Wait、DATA_READY、复核owner/屏障/Task后提交，再Finish。只在完整工具组和已持久化的最终诊断触发，初始化告警不备份；网络等待计入原Task截止时间。

清理用Runner原有独立窗口：先核对当前attempt及是否曾认领，随后Abort原维护job，确认停止，未提交项记FAILED，最后Finish及释放原owner。停止不明仍阻止换Pod；不创建新job掩盖未知结果、不forget孤立快照。取消在DATA_READY后先落盘时拒绝COMMITTED，已经提交的历史点不因后续取消撤销。

### 最终诊断为什么单独保存

模型原文与对外诊断可能不同：Runtime会按证据、状态时效和策略降级公开结论。保留模型原文，再追加明确标为Runtime生成的AI消息，内容包括`runtimeSource=final-diagnosis`、原`runtimeTaskId`和`runtimeStatus`；独立JSONL消息元数据`runtimeGenerated=final-diagnosis`是恢复识别依据。Provider返回的同名additional_kwargs会在Loop剥离，不能只因模型文本自报标记就跳过调用。

最终诊断已保存、最后备份失败后，同Task自动接续可直接采用该Runtime节点，重新核对状态时效并完成检查点，不重放模型或历史工具；额度、原deadline不回滚。手动resume创建新Task，不能用旧Task诊断短路。存在未应用的持久控制命令时仍进入Loop；最终备份期间已确认的follow-up/steer不能在成功状态被遗漏，TaskStore在相同接受锁下决定接续，必要时同Task新attempt，仍受原预算/截止时间及最多2次恢复限制。

恢复诊断以及最终备份等待后都会检查60秒状态时效。若已过期，追加“历史诊断、当前状态未核实”的安全视图；备份后降级最多补一次检查点，沿用原时间预算，没有额外模型调用。历史模型原文和早期检查点仍保留，不声称历史结论现在仍成立。

普通消息仍受16Ki字符持久化限制；Runtime最终诊断使用独立128Ki字符、深度8、集合64项的结构化JSON预算并逐叶脱敏，防止多条证据被截断成无法恢复的半个JSON。超限明确失败；这不扩大模型输入预算，Session引用仍最多2MiB/2048节点。原始诊断与Session都是公开脱敏内容，不存隐藏思维或凭据。

### 安装、启动与独立演示

先完成§4的固定镜像构建、专用MinIO仓库初始化、Secret与NFS准备；不要复用RAG bucket。两终端共享模式选择，凭据从现有仓库外配置注入：

```bash
export AGENTD_CHECKPOINT_MODE=restic
export AGENTD_CHECKPOINT_REPOSITORY_ID=repo-workspace
# 设置已核对的SANDBOXD_BACKUP_SECRET_NAME、SANDBOXD_BACKUP_MINIO_IP。
# Go终端仍需独立Agent/Operator凭据及SANDBOXD_WORKSPACE_CONTROL_DIR。
bash hack/workspace.sh build
bash hack/workspace.sh go
# agent终端仅传Agent所需凭据，不能带Operator Token；设置AGENTD_RECOVERY_DIR。
bash hack/workspace.sh agent
```

统一入口的restic分支已增加配置与语法检查，尚未通过该shell分支启动整套服务；以下独立演示实际使用Go二进制和相同flags启动，二者证据不混淆。退出V2时默认`off`只关闭新备份，不能让旧二进制写入含V2事件的Go控制目录。

```bash
go build -o .cache/workspace-sandboxd-v2 ./cmd/sandboxd
agentd/.venv/bin/python -m agentd.workspace_checkpoint_demo \
  --secret-name "$SANDBOXD_BACKUP_SECRET_NAME" \
  --minio-ip "$SANDBOXD_BACKUP_MINIO_IP" \
  --output .cache/agent-joint-checkpoint.json
```

演示使用真实TaskStore/Runner/Go HTTP/gVisor/NFS/restic/专属MinIO，模型为固定Replay，外部模型调用0。写报告→读报告→最终诊断，检查3个COMMITTED点、父链/revision、完整snapshot ID、可读Session引用、Task/工作区/镜像身份、3模型槽/2工具槽和实际NFS服务端hash；清理前观察维护Pod实际CRI runtimeType。每次独立原生Linux控制目录，拒绝覆盖已存在输出，保留所有文件、快照、Secret与控制日志。

### 当前证据与待办

`phase9-v2-agent-joint-checkpoint.json`和`-v2.json`均为实际正常路径成功；第二轮包含Runtime来源元数据及原Task标记断言。最终代码第三轮`phase9-v2-agent-joint-checkpoint-v3.json`通过，27,881.23ms，仍为3个COMMITTED点/3模型槽/2工具槽；没有伪造故障、删数据或外部模型调用。后续回归结果见PROGRESS及机器可读索引。早期全量223/223，随后全量231/231（288.359秒）；后者启动之后新增的预检/诊断预算/状态时效不能用该批全量冒称覆盖。最终时效定向52/52、9.409秒通过，含两项过期降级与额外调用为0断言。最终代码完整Python235/235、295.378秒通过，见`phase9-v2-agent-checkpoint-latest-full-tests.txt`；各批有重叠，不累加成绩。首次late-control命令误引用不存在的test_runtime模块，原始FAILED输出保留；修正后33/33通过，不改写失败记录。

尚未完成：真实进程在PREPARING/DATA_READY/COMMITTED前后中断、真实取消/MinIO异常/仓库锁、restore新空工作区屏障及镜像校验、精确历史分支、引用保护/清理预览/受控forget/prune、十项V2完整验收、同条件V1/V2性能与流量测量。正常联合保存不是上述故障和恢复的替代证据，Goal仍active。

## 8. 联合提交切点的真实Agent进程实验

`agentd.workspace_checkpoint_fault_demo`是独立管理夹具，不向生产Runtime或HTTP API增加故障开关。第一进程仍调用实际create_app/TaskStore/Runner/协调器，只在本夹具子进程中包裹指定方法；在真实操作前/后发布私有fsync标记并SIGSTOP。父进程用waitpid确认OS已停止，再向自己启动的该进程发送SIGKILL，检查返回码-9，然后用原生控制目录启动没有切点包装的第二个普通Agent进程。Go服务及NFS/MinIO持续运行；不是Go进程崩溃、节点分区或历史文件还原实验。

固定切点如下：

| 参数 | 实际停止位置 | 自动接续要求 |
|---|---|---|
| reserved | Go Reserve返回、Session引用/PREPARING之前 | 仅Go屏障存在；不能凭空制造Python检查点，先对账释放原job |
| preparing | PREPARING真实fsync之后、Start之前 | 不能作为恢复点；原点失败后沿NFS最新目录接续 |
| before-data-ready | restic成功返回、DATA_READY尚未写入 | 仍为PREPARING，原job可读到快照结果但不能推进head |
| data-ready | restic成功、DATA_READY真实fsync之后 | 文件已备份不等于提交；原点不能成为head |
| before-commit | 已核对owner，在短锁内调用commit之前 | 仍为DATA_READY，不能暴露为COMMITTED |
| committed | COMMITTED真实fsync后、Go Finish之前 | 重建必须保留原提交/head/revision；先Finish再换attempt |

每轮独立Task/工作区/控制目录，模型固定为写报告→读取→最终诊断。原调用表、Task/session/branch/workspace、provider、阶段时钟、额度和deadline逐项比对，旧写入仅一次；Session前缀保留，工具调用与结果各出现一次。父进程在原进程退出后才取得检查点账本锁，确认未提交点拒绝restore_point、已提交点可读取Session引用。恢复后的head/revision由原始JSONL重建，不修改日志造状态或补时间。

```bash
# 前提同§7；10880/18090空闲，仅本项目单节点和专用存储。
agentd/.venv/bin/python -m agentd.workspace_checkpoint_fault_demo \
  --phase data-ready --secret-name "$SANDBOXD_BACKUP_SECRET_NAME" \
  --minio-ip "$SANDBOXD_BACKUP_MINIO_IP" --output .cache/checkpoint-cut-data-ready.json
# 可分别选择reserved/preparing/before-data-ready/before-commit/committed；串行执行，输出路径不可复用。
```

首个实际`phase9-v2-agent-cut-data-ready.json`通过：真实SIGKILL、原Task自动接续2个attempt、原未提交点变FAILED、最终head revision2；原模型槽保留并共3模型/2工具槽，写/读各1次，NFS服务端hash一致。杀进程至API ready约57,013.97ms、至最终核查73,746.66ms，包含Windows挂载盘Python启动等待及核查/清理，不是生产RTO。DATA_READY时维护Pod的CRI实际为runsc。随后`phase9-v2-agent-cut-committed.json`也通过：原COMMITTED点及revision1在重启后保留，最终head revision3；原Task/额度/时钟保持、写/读各1次，kill→API ready50,588.48ms、→核查66,022.02ms。其余切点按PROGRESS和逐个原始报告判断，不能从部分成功推断整项V2-04已完成。

`phase9-v2-agent-cut-reserved-v2.json`也通过：Go已冻结而Python尚无检查点记录，重启后仍不制造该ID的Python记录；先对账旧reserved job/业务owner，再同Task新attempt接续，最终head revision2，原调用/时间预算保持。该切点Start尚未发生，维护Pod为空是正确现象，不伪造runsc维护证据；业务Pod实际gVisor/NFS仍核验。kill→API ready55,176.11ms、→核查72,445.33ms。

RESERVED首轮在端口预检即报EADDRINUSE，未创建控制目录、Go或Agent，只有原始失败输出`phase9-v2-agent-cut-reserved-tests.txt`；整理快照`phase9-v2-agent-cut-reserved-preflight.json`明确唯一根因未证实。随后无监听者，端口probe设置SO_REUSEADDR，且真实本机监听拒绝单测通过；第二轮用新路径，不覆盖首次失败。

本批完整Python239/239、282.089s通过`phase9-v2-joint-fault-full-tests.txt`，包含五类切点包装与启动默认兼容；此后端口probe抽取及额外本机监听用例由最终5/5、0.068s覆盖`phase9-v2-joint-fault-fixture-final-v2-tests.txt`，不将239项冒称包含后加用例，也不相加。中间4/4、0.187s报告单列。

三个真实切点的独立报告一致性审计分别以`*-audit.json`保存，附原报告SHA256；它们是只读核对，不算额外三次服务实验。后续PREPARING后、COMMITTED前及备份完成但DATA_READY前也已真实通过，六个切点汇总见`phase9-v2-joint-six-cut-summary.json`。这证明Agent进程级协议切点恢复；Go进程崩溃、真实取消/存储异常、历史恢复和引用GC未由这些实验验证，完整V2验收仍未完成。

夹具首轮单测29项1 error：模拟Popen.pid未设置整数导致状态打印无法JSON序列化；原始phase9-v2-joint-fault-fixture-tests.txt保留。修正后29/29、7.051s通过phase9-v2-joint-fault-fixture-v2-tests.txt；后续增加reserved包装的单测和完整回归分别记录，不能将29项冒称覆盖后增加用例。测试的暂停信号在单测中被Mock，不冒充真实进程实验。

本批新增三轮：preparing的kill→API ready55,281.38ms、→核查71,626.33ms；before-commit为55,903.56/71,971.31ms；before-data-ready为54,865.64/72,153.67ms。最后一轮由父进程对原job执行只读Wait核对实际完整snapshot ID，而Python点仍为PREPARING/data=null；重启后FAILED，最终head revision2，不提升孤立快照。所有原Task/调用/时间预算保持、原写入各仅一次。三轮使用原固定Go二进制，未用新恢复控制模块冒充实际恢复。最新夹具/协调器Python19/19、3.617s通过`phase9-v2-joint-six-cut-tests.txt`；既往239全量是前一批，不冒称含新切点代码。

## 9. 新空恢复目标的控制日志约束（本节记录先前逻辑验证）

新增Go `restore_barrier.go`，复用已有WorkspaceRegistry/持久屏障，不复制一套生命周期、数据库或控制服务。`RestoreSource`固定完整snapshot ID、manifest SHA256、仓库crypto ID、业务imageRef和实际imageID；同一job重试不能换来源。它不是自由PodSpec，维护镜像、Secret和endpoint仍来自可信部署配置。

`reserveRestore`只接受ready/generation0、从未认领的新目标；原工作区不会被覆盖。来源与job先写Go独占/fsync JSONL，再由未来的可信执行器创建恢复维护Pod。目录内容是否真的为空还必须由固定helper在写入前检查，本模块的“未认领”不等同于已经验证空目录。

恢复失败或未核验时，维护Pod确认停止后可释放全局维护占用，但目标仍保持不可认领：`restoreSource != zero && !restoreVerified`。不删失败目录，不换另一个快照覆盖它；新恢复请求应使用新的workspace ID。其他工作区不因已停止的失败任务永久阻塞。执行者停止不明时仍保留原有屏障，不能据超时/Pod NotFound放行。

可信执行器读取并验证实际repo/snapshot/manifest成功结果后，先用verifyRestore将已核验标志fsync到stopped屏障（仍冻结），再清理维护Pod；completeRestore随后将不可变restoredFrom来源写入workspace记录并释放屏障。证明、来源与解屏障之间的中断均可按同来源接续；来源不能移除或更换。只看到容器停止而没有已核验证明时，不能直接把目标当作恢复成功。业务Pod认领须使用原imageRef，并通过现有Bind路径核对实际imageID，不能用不同镜像静默继续。

零值字段使用项目Go1.26.5支持的omitzero，不改变旧backup/无恢复记录的JSON字段；新程序可读原记录。历史无配置restore逻辑夹具保持读取兼容，不将它当作真实已核验恢复。出现新恢复字段的日志不能交给旧严格解析二进制继续写；不改写旧日志或原数据。

5个新增逻辑用例覆盖：来源幂等/禁止改变、重开仍冻结、失败目标隔离而其他目录可运行、来源与实际业务镜像固定、零值编码，以及来源已fsync但屏障未释放的中间重开。Pod/仓库身份为合成夹具，停止证明也由状态机夹具构造，**不是真实restic恢复或真正进程崩溃**。

原始Go sandbox/API与race分别见`phase9-v2-restore-control-tests.txt`、`-race-tests.txt`；追加中间落盘用例后最终race见`phase9-v2-restore-control-final-race-tests.txt`，Go全项目回归见`phase9-v2-restore-control-full-go-tests.txt`。本轮真实Agent切点仍使用上一批固定Go二进制，新恢复字段未接运行中服务，不能用切点报告声称验证了本模块实际恢复。

上述执行器及基本真实服务验证后续见§10；受限管理接口、Session精确父链与新分支接线、引用保护和受控清理仍待完成。内部completeRestore不是对外成功证据，也不能将“容器停止”直接当作“文件完整恢复”。

## 10. 可信恢复执行器与真实NFS/MinIO验收

`BackupRuntime`复用同一套维护Pod归属/UID/spec检查、submitted先落盘、节点健康与容器终止证明。新增ReserveRestore / StartRestore / FinishRestore，Wait / Abort共用原逻辑；没有新增模型工具、自由PodSpec或任意命令。源repo/snapshot/manifest/业务镜像在预留时固定，只写新ready/generation0目标；原NFS目录不覆盖。

### 写入前后验证与清理顺序

固定helper新增四参数restore：`restore <full_snapshot_id> <manifest_sha256> <repository_crypto_id>`。读取仓库config并比较完整crypto ID发生在任何文件恢复写入之前；随后检查目标实际为空、完整snapshot及manifest tag，restic restore --verify，最后重新计算文件manifest。结果包含仓库身份供Go再次比较。旧三参数管理组件保留兼容，新可信恢复路径始终传非空完整仓库ID。

```text
新空工作区与来源 → 持久屏障 → submitted → 受限gVisor维护Pod
  → helper仓库身份/空目录检查 → restore/verify/manifest
  → Wait验证真实成功结果和repo/snapshot/manifest
  → verifyRestore fsync（stopped + verified，仍冻结）
  → UID条件清理维护Pod → completeRestore fsync来源/解屏障
  → 业务Pod认领并核对原imageRef及实际imageID
```

FinishRestore(success=true)会自己读取/核对真实结果，不能只信调用方自报成功。已持久化核验证明后，即使清理Pod后中断，也可以从同job证明接续；没有证明时Pod NotFound不能代替核验。失败结果只能在确认停止后清理，并保留目标隔离，不能将半恢复目录认领为成功。此处恢复控制与原Task预算账本分别负责各自数据，尚未接入历史Task/Session恢复。

新维护镜像`sandboxd-restic:0.18.1-v2`增加仓库身份参数，restic二进制仍0.18.1且SHA256不变。v1/业务runtime-v1.5及旧数据保留。Docker inspect Id=`sha256:0d07dc6898e243106416570c005bd094ef86dec618cd57ef2c25677351468705`；实际维护CRI imageID=`sha256:146dcc0f65c9a1c7839494f74b7724f005a493974d27a9d41eff19759856ce58`，两者分别标注，不混称。构建/load原文见phase9-v2-restore-image-build/load.txt。

### 可复现组件入口

前提：既有kind/gVisor/NFS/专属MinIO与仓库Secret可用，source-report是此前真实COMMITTED合成证据。仓库自带的旧报告只对应本机保留的实验仓库；新环境必须先用§8的committed场景生成自己的来源报告，不能把旧snapshot ID当成新MinIO里已有的数据。只传Secret名称，不打印值；原生控制目录必须是此夹具的新UUID目录。

```bash
export GOMODCACHE=/tmp/sandboxd-workspace-go-mod GOCACHE=/tmp/sandboxd-workspace-go-build
export PATH="$HOME/.local/bin:$PATH"
mkdir -p .cache/restic-image-v2
CGO_ENABLED=0 go build -o .cache/restic-image-v2/sandbox-restic ./cmd/sandbox-restic
cp "$HOME/.local/share/sandboxd-tools/restic-0.18.1/restic" .cache/restic-image-v2/restic
docker build --network=none -f deploy/workspace/restic.Dockerfile \
  -t sandboxd-restic:0.18.1-v2 .cache/restic-image-v2
kind load docker-image --name sandboxd sandboxd-restic:0.18.1-v2
go build -o .cache/workspace-restore-runtime-smoke ./hack/workspace-restore-runtime-smoke
# 新环境先生成当前仓库中的真实COMMITTED来源；需§7的Go二进制/原v1镜像。
go build -o .cache/workspace-sandboxd-v2 ./cmd/sandboxd
agentd/.venv/bin/python -m agentd.workspace_checkpoint_fault_demo --phase committed \
  --secret-name "$SANDBOXD_BACKUP_SECRET_NAME" --minio-ip "$SANDBOXD_BACKUP_MINIO_IP" \
  --output .cache/restore-source.json
# SANDBOXD_RESTORE_PROBE_DIR=$HOME/.local/share/sandboxd/workspace-restore-runtime-smoke/<新的32位UUIDhex>
.cache/workspace-restore-runtime-smoke -nfs-server "$SANDBOXD_NFS_SERVER" \
  -minio-ip "$SANDBOXD_BACKUP_MINIO_IP" -secret-name "$SANDBOXD_BACKUP_SECRET_NAME" \
  -control-dir "$SANDBOXD_RESTORE_PROBE_DIR" -source-report .cache/restore-source.json \
  -output .cache/restore-probe.json
# 失败后只用原control-dir对账，不强删finalizer或新建job掩盖原结果。
.cache/workspace-restore-runtime-smoke -reconcile -nfs-server "$SANDBOXD_NFS_SERVER" \
  -minio-ip "$SANDBOXD_BACKUP_MINIO_IP" -secret-name "$SANDBOXD_BACKUP_SECRET_NAME" \
  -control-dir "$SANDBOXD_RESTORE_PROBE_DIR" -source-report .cache/restore-source.json \
  -output .cache/restore-reconcile.json
```

`phase9-v2-restore-runtime-component.json`真实通过两场景：

- 正常：已有合成COMMITTED快照恢复到新空NFS工作区，文件manifest及报告hash核对；新业务Pod读回与NFS服务端hash一致，业务imageID匹配来源。维护与业务CRI均实际runsc；来源与verified控制记录落盘后才允许认领。
- 错误仓库身份：实际启动同样受限的维护Pod，helper在写入前拒绝；目标仍为空、BeginClaim拒绝，停止/清理后目标继续隔离。它测试错误期望ID，不宣称已更换Secret或模拟凭据轮换。

业务/维护Pod均经Go清理，NFS目录、PV/PVC、快照、Secret及控制日志保留。后续已停止夹具-reconcile通过，控制日志hash不变（phase9-v2-restore-runtime-reconcile及audit.json）；只是幂等对账，不是新的故障成绩。

### 回归与剩余范围

初始helper/sandbox/API race通过phase9-v2-restore-runtime-initial-tests.txt；最终逐项race见-final-race-tests.txt；新增入口后Go全项目先后见-full-go-tests.txt、-final-full-go-tests.txt。新增4个恢复Runtime顶层测试（含repo/snapshot/manifest三个子场景）、1个helper契约测试；与5个控制状态测试有重叠，不累加成服务验收数。管理对账入口一次编译因误用List方法失败，修正为既有Records后构建、真实幂等对账和最终全仓回归通过；未改写先前真实恢复报告。本批没有Python生产代码改动，未重跑Python全量，不冒称旧239项覆盖本批Go行为。

**本节当时尚未完成**（HTTP/客户端后续见§11）：会话父链与新branch/workspace联合接线、恢复中的Task原预算与取消、恢复引用保护/清理预览/受控forget-prune、真实存储异常/仓库锁、同条件性能及完整V2矩阵。Go工作区元数据中的branchId不等于已经创建Python会话分支；本组件不加载Session引用，也不重放模型/工具。

## 11. 受限恢复 HTTP 与 Python 客户端

可信文件恢复已通过 Python 客户端接入实际 Go HTTP。仅在受信任备份配置完整时启用，沿用 Agent Token；Operator Token 不授予此接口权限。它不是 LLM 工具，也尚未接入历史会话分支 API。

- `GET /api/v1/workspaces/{workspace}/restore` 读取该目标的恢复屏障。
- `POST /api/v1/workspaces/{workspace}/restore/{action}` 只接受 reserve/start/wait/abort/finish/fail。输入绑定目标 session/branch、UUIDv4 checkpoint、固定 restore job 和完整 snapshot/manifest/repository/image 身份，不接受 argv、仓库地址、Secret、执行镜像或 success 自报。
- Python 校验固定字段和有界超时，不自动重试可能已执行的请求；Go 独立拒绝未知字段和身份不一致。客户端校验不是可信边界。
- wait 超时保留屏障；abort/finish/fail 使用独立30秒清理窗口，HTTP断连不撤销必要清理。finish 必须先有 stopped 证明，不在清理窗口等待仍执行中的恢复任务，避免绕过上层截止时间。
- fail 只释放全局维护占用，失败目标继续隔离；finish 再次核验真实结果及持久证明，成功才放行。重复 finish 幂等。

### 真实验收与复现

`phase9-v2-restore-http-component.json` 两场景通过：正常来源恢复到新空NFS目录，Python文件RPC与NFS服务端hash一致、来源业务imageID一致、实际维护/业务runsc；错误期望仓库在写入前拒绝，目录仍为空，调用方请求finish不能提升为成功，claim仍拒绝。无Token读取401、绕过Python提交success字段400、错误branch清理409、成功finish重试均在真实HTTP链路核查。没有测试真实连接中断；取消请求context后的清理由单元测试覆盖，两种证据分开。

前提与§10相同：固定v2维护镜像已载入kind，NFS/专属MinIO与Secret可用。新环境先按§10生成自己的`.cache/restore-source.json`，不要复用公开报告中的旧snapshot ID。确认10880空闲后执行：

```bash
export GOMODCACHE=/tmp/sandboxd-workspace-go-mod GOCACHE=/tmp/sandboxd-workspace-go-build
export PATH="$HOME/.local/bin:$PATH"
go build -o .cache/workspace-sandboxd-restore-http ./cmd/sandboxd
agentd/.venv/bin/python -m agentd.workspace_restore_http_demo \
  --source-report .cache/restore-source.json \
  --secret-name "$SANDBOXD_BACKUP_SECRET_NAME" --minio-ip "$SANDBOXD_BACKUP_MINIO_IP" \
  --output .cache/restore-http-new.json
```

夹具生成独立原生Linux控制目录，拒绝覆盖报告；仅对自身已登记目标进行终止核查/清理，保留文件、快照、Secret及控制记录。现有管理Go夹具和旧默认v1备份夹具行为保持兼容。

客户端/备份客户端/远程文件14项本地测试通过（MockTransport，0.634秒）；Go全项目与最后API/sandbox race原文分别见 `phase9-v2-restore-http-full-go-tests.txt`、`phase9-v2-restore-http-final-v2-go-tests.txt`。最后新增断连context单测由后者覆盖，早先全项目运行不冒称包含它。完整Python243/243、352.315秒通过，原文 `phase9-v2-restore-http-full-python-tests.txt`；此前14项与全量有重叠，不相加。

**下一步**：精确Session父链、新branch/workspace和持久恢复请求联合接线，先保护活动恢复源；恢复后的预算/取消/不重放、引用清理、真实存储异常与同条件性能仍需验收。当前真实成绩仅证明文件恢复HTTP组件，不能宣布V2完整完成。

## 12. 历史恢复来源引用与分支父检查点（控制模块）

历史恢复使用同一份单写入检查点JSONL新增`checkpoint.restore`事件，保持Task账本与Go归属日志各自唯一写入者。这里只登记控制状态；可信文件恢复HTTP、Task接续和取消必须由后续协调器核对，状态名不能代替这些真实证明。

```text
RESERVED → FILES_READY → ATTACHED
    └────────┴────────→ FAILED
```

- 首次`prepare_restore`先持久化UUIDv4 restore/branch/workspace、预分配taskId及COMMITTED sourceCheckpointId，再允许协调器调用远程恢复。Session必须相同，目标branch/workspace/task必须独立且没有被旧检查点或另一恢复请求使用。
- 同请求重试复用绑定，不追加重复记录；换来源或换目标明确拒绝。FILES_READY要求上层已完成可信文件验证；ATTACHED要求上层已持久化Task绑定。本节记录接线前控制模块；后续真实Task/API接线见§13，不能将本节合成状态转移单独当成真实联合恢复。
- `protected_checkpoints`返回分支恢复头和所有保留恢复请求的来源引用。FAILED也保留引用及隔离目标，防止结果不明时遗忘来源；当前没有自动解除引用、forget/prune或管理清理入口。后续清理必须消费此投影，并按仓库/完整snapshot聚合引用，不能只按checkpointId删除共享快照。
- 仅ATTACHED建立新分支来源。新分支首份检查点的parentCheckpointId指向所选来源，revision从1开始；来源分支继续前进不改变这个关系。首份提交核对目标workspace/task和不可变Session父链确实包含来源的完整前缀。后续提交沿新分支head递增，不把来源revision拷贝到新分支。
- 首份检查点准备/提交及重建都使用相同的来源关系；COMMITTED仍是一条日志同时投影head/revision。旧日志没有restore事件时按原逻辑读取。出现新事件的控制目录不能交给旧二进制写入，旧解析器会拒绝未知事件。

例如来源分支已到revision2，新分支第一次保存是：

```text
Session
├── branch-A → cp-A2 (revision=2)
└── restore-R (source=cp-A2, workspace-B, task-B)
    └── branch-B → cp-B1 (revision=1, parent=cp-A2)
```

原branch-A、原工作区和原Task审计不回退；本模块也不修改Task预算。手动历史分支仍须创建新Task，自动故障接续仍须保留原Task/额度/截止时间，两者不能混用。

验证入口：

```bash
agentd/.venv/bin/python -m unittest agentd.tests.test_checkpoints agentd.tests.test_checkpoint_coordinator -v
```

初轮34项本地测试通过（5.804秒），`phase9-v2-restore-reference-tests.txt`；随后增加来源head前移后的引用、非法完整restore日志、revision2来源的新分支序号，以及恢复请求列表读取，最终35/35、6.079秒通过`phase9-v2-restore-reference-final-tests.txt`。完整Python249/249、289.899秒通过`phase9-v2-restore-reference-full-python-tests.txt`，包含六项新恢复控制用例；有重叠的测试批次不相加。所有身份/快照/状态证明为合成，无真实restore/Task分支/API或清理成绩；旧真实服务报告不会自动覆盖本批新控制代码。 另以新解析器读取已保留的真实Agent控制目录，所选COMMITTED检查点与最终revision3头一致，JSONL和Session引用字节不变；结果`phase9-v2-restore-reference-retained-compat.json`只证明旧格式重建兼容，不是新增故障或恢复服务验收。

**当时的下一步**（已在§13接线；实际Task会先持久化queued意图，Worker完成恢复后才允许Runner执行）：持久历史恢复协调器与TaskStore接线：读取精确Session引用，先RESERVED，再调用既有Go恢复HTTP验证新空目标，成功后FILES_READY，持久化新Task/Session分支并ATTACHED再入队。重启须按原restoreId/job/目标对账；不可重新分配ID或把FILES_READY直接当Task可运行。原分支有活动任务时拒绝切换，先保持单Worker会话head语义；随后补真实历史分支及取消/预算/引用清理验收。

## 13. TaskStore、Agent API与真实历史分支

### 为什么先登记queued Task

用户需要在恢复过程中取消，因此按下列顺序保存。提前创建的是有预算的执行意图，**没有提前运行模型、文件工具或认领业务Pod**：

```text
Agent Token + checkpointId + restoreId
  → RESERVED：固定来源、新branch/workspace/task并保护来源
  → 持久化queued Task，再返回202（相同restoreId重试返回同Task）
  → Worker启动attempt和原claim时钟
  → 可信Go恢复到新空目录，核验snapshot/manifest/repo/image
  → Go核验结果持久化并释放维护屏障
  → FILES_READY
  → 校验精确Session父链，选择该节点，ATTACHED
  → Runner认领业务Pod、继续诊断并保存新分支检查点
```

网络恢复不持有Task取消锁；ATTACHED与接受取消在短锁内排序。Session切换前再次核对当前Task未取消、未过期。已经ATTACHED的Task故障接续沿最新NFS状态继续，不重新下载旧快照覆盖后来文件。默认resume和Session读取依据最后可用Task的分支/工作区及持久完整节点；未ATTACHED或失败的恢复意图不成为默认路线。即使Session选择已追加、ATTACHED前失败留下旧节点head，也不将旧上下文与原分支最新文件混用；保留原JSONL审计，由新的合法继续选择正确父链。

`RestoreCoordinator.ensure_files`限制整段恢复最多90秒，且每一步受同Task持久截止时间限制；已有claim/loop分段时钟保持。重启复用原restoreId/job/目标，先核查旧维护Pod与已验证结果；已完成的FILES_READY只核查，不重复restore。停止无法确认时不发放新attempt。独立清理窗口只用于终止/释放，不追加执行预算。

启动时RESERVED存在而Task缺失，按原ID补建意图；FILES_READY/ATTACHED却缺少原Task账本则拒绝，不伪造其历史。queued缓存若对应旧活跃attempt，取消响应保持cancelling，等停止证明后才进入cancelled。失败和取消保留来源引用、目录和审计；没有自动forget/prune。

### 代码阅读与面试自测

按`main.py`恢复路由→`store.py`排队/取消/启动对账→`runtime/restore_coordinator.py`→Go `restore_runtime.go`→检查点日志读这一条路径，不需要新增Agent框架。

- **queued是否等于可以执行？** 只表示已接受意图；FILES_READY核对文件、ATTACHED核对会话，随后才允许Runner。
- **两个服务的日志是否是一个事务？** 不是。各自fsync和单写入，用固定请求ID、显式状态、可信证明及启动对账跨窗口恢复；不宣称分布式原子提交。
- **为什么不能恢复后总是再pull一次？** ATTACHED后的工作区可能已有后续写入，重下旧快照会丢失新内容；自动接续必须查询当前NFS并沿用原预算。
- **为什么失败Task不能作为默认resume来源？** 它可能只登记了目标，或会话刚选节点就失败。默认路线由可用Task分支及持久完整节点投影，避免拼接错误上下文与文件。

### API和模式边界

沿用Agent API Token：

| 接口 | 行为 |
|---|---|
| `POST /api/v1/sessions/{session}/checkpoints/{checkpoint}/restore/{restore_uuid4}` | 明确选择COMMITTED检查点，返回202、taskId、restoreId及新branch/workspace；首次请求前由客户端生成并保留UUIDv4，重试复用 |
| `GET /api/v1/sessions/{session}/restores/{restore_uuid4}` | 只读恢复状态、来源及目标身份 |
| `POST /api/v1/tasks/{task}/cancel` | 既有持久取消，适用于恢复中的新Task |
| `POST /api/v1/sessions/{session}/branch/{node}` | restic模式下要求该节点有唯一COMMITTED版本；没有或有多个版本则409，改用明确checkpoint接口；响应附restoreId |
| `POST /api/v1/sessions/{session}/resume` | 最新工作区继续，不隐式回滚历史快照 |

同Session有未完成Task时拒绝另开工作区分支，保持单Worker/活动Session head语义。off/V1仍拒绝旧节点文件恢复；不会因为代码中有restorer就静默打开后端。无快照、来源缺失/校验失败返回明确错误，不使用相邻版本。控制盘整体丢失恢复不在本轮承诺内；这里要求原Session父链与不可变引用一致，损坏时拒绝猜测。

统一入口的restic模式固定维护镜像已更新到`sandboxd-restic:0.18.1-v2`，需先按§10构建/载入；off行为保留。已登记v1 Job配置的旧组件控制目录不能直接改镜像绕过固定配置，保留旧目录/镜像，演示使用独立新控制目录。完整shell restic启动仍需另行验收，本节真实夹具直接使用同样Go flags。

### 可重复的两分支实验

前提：§10的v2维护镜像、Go二进制、可用NFS/专属MinIO/Secret，10880空闲。先按§10生成本环境`.cache/restore-source.json`；不能把仓库旧报告的snapshot ID当成新MinIO已有数据。

```bash
export GOMODCACHE=/tmp/sandboxd-workspace-go-mod GOCACHE=/tmp/sandboxd-workspace-go-build
export PATH="$HOME/.local/bin:$PATH"
go build -o .cache/workspace-sandboxd-restore-http ./cmd/sandboxd
agentd/.venv/bin/python -m agentd.workspace_restore_branch_demo \
  --source-report .cache/restore-source.json \
  --secret-name "$SANDBOXD_BACKUP_SECRET_NAME" --minio-ip "$SANDBOXD_BACKUP_MINIO_IP" \
  --output .cache/restore-branches-new.json
```

夹具把自身已保留的合成来源控制目录复制到新原生UUID目录，原目录不写；两次使用不同Replay脚本和两次正常App生命周期。仅在夹具构造器包装实际SDK以观察RPC/CRI，不替换处理器、TaskStore/Runner或真实服务。Agent入口是实际ASGI处理器，Go是实际TCP HTTP；**没有宣称本轮验证Agent TCP/进程SIGKILL**。

`phase9-v2-restore-branches-component.json`真实两场景通过，总69,547.90ms（含观测、恢复和同步保存，不是生产RTO）：

- 第一分支：从旧COMMITTED点恢复新空工作区，读原报告、显式新建branch-only.md、输出最终诊断；3模型槽/2工具槽，3个新COMMITTED点，首份parent指向所选来源且revision1，head revision3。
- 第二分支：同一旧点再恢复另一空目录，仅读原报告与诊断；2模型槽/1工具槽、head revision2，目录没有第一分支新增文件，精确Session父链/最终引用不含兄弟标记。报告第一场景siblingMarkerAbsent=false表示该场景没有要求标记缺席；第二场景true才是隔离断言。
- 两次新task/branch/workspace互异，原Task完整账本状态、原分支恢复头、原来源控制文件hash不变。新工作区报告hash与原NFS一致，实际维护CRI均runsc，业务镜像身份/启动gVisor/NFS挂载核对。相同请求重试同Task、401及状态查询由实际处理器验证。
- 原始文件/目录/快照/Secret保留，业务和维护Pod均正常清理。独立只读Go/Python日志checksum/seq审计`phase9-v2-restore-branches-audit.json`通过：每请求仅一次RESERVED，FILES_READY→ATTACHED早于新分支PREPARING，Go核验证明→释放→业务认领顺序正确。审计不是第三次服务实验。外部模型0。

### 回归、限制与下一步

首轮52/52、10.589秒通过，随后API鉴权/未知停止等55/55、11.551秒通过，原始`phase9-v2-restore-store-initial-tests.txt`与-final-tests.txt。此后增加节点歧义、ATTACHED后未知写确认恢复不重新下载快照，以及启动取消缓存修复，完整Python258/258、290.382秒通过`phase9-v2-restore-store-full-python-tests.txt`，含当时9项恢复Task用例；后加启动取消缓存修复/第10项用例由最新定向58/58、12.618秒覆盖`phase9-v2-restore-store-latest-tests.txt`，不把258项冒称包含后改动，批次不相加。随后失败默认路由/Session选择窗口修正由最终59/59、12.697秒定向和最终完整Python260/260、288.855秒覆盖（`phase9-v2-restore-store-routing-tests.txt`、`phase9-v2-restore-store-routing-full-python-tests.txt`）；真实正常两分支报告不冒充此故障窗口的真实服务实验。真实取消/恢复SIGKILL、存储失败、引用清理/性能与完整十项验收仍待完成。

**第一项下一步**：增加真实恢复窗口取消/进程切点和存储异常/锁；再接引用保护驱动的管理预览与受控forget/prune、同条件性能、统一restic入口及完整矩阵。历史正常分支已通不等于所有故障已验证。

## 14. 官方参考与版本

- [restic 0.18.1 release](https://github.com/restic/restic/releases/tag/v0.18.1)：固定二进制与校验表。
- [0.18.1 MinIO/S3配置](https://restic.readthedocs.io/en/v0.18.1/030_preparing_a_new_repo.html#minio-server)：S3兼容仓库、凭据、路径式访问；初始化可创建专用bucket。
- [0.18.1脚本接口](https://restic.readthedocs.io/en/v0.18.1/075_scripting.html)：JSON结果与退出码。

二进制本身的`restore --help`也确认snapshotID:subfolder语义；版本化文档无法通过浏览器加载的恢复页面不当作已读取证据。
