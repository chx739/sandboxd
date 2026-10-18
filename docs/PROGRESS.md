# sandboxd 持续开发进度

> 本文件是跨会话进度快照。最高目标以 ../GOAL.md 为准；当前学习与项目口径以 README.md、docs/README.md 和 24-项目全景与心智模型.md 为准。

## 当前状态

Phase 1–6 已完成并保留；Phase 7 的会话、记忆、ES/Milvus 检索和 Jev 代码及证据保留在 [PR #1](https://github.com/chx739/sandboxd/pull/1)。**当前为用户 2026-09-29 批准的 Phase 8 Kubernetes 运维 Agent MVP**；完整目标和验收见 [37](37-Kubernetes运维Agent目标与验收.md)。用户最新已更新 Key 并重新授权 Jev 最小路由，覆盖此前暂停。历史 Jev 首题与 TypeSafe 401 只作旧证据。Phase 8 尚未完成。

当前实施目标：**Phase 9 工作区持久化与自动恢复**，用户于 2026-10-17 明确授权按文档 43 开始实现并进入目标模式。Phase 8 的未完成审核/Live 保留，本轮不执行付费模型请求。

当前分支：

    codex/workspace-restic-v2（基于V1 9c91c9b；前置PR#2依赖PR#1，均未合并）

当前里程碑：

    Phase 9 M5：V1已交付；V2保存/协议切点/历史分支/存储故障/联合GC/固定后端性能及统一owned部署接续已验证；十项完整验收审计待完成；Goal active

## Phase 9 当前恢复点

### 2026-10-18：owned V2初始化与统一入口接续已真实通过

- 起点4e7930a，仍为codex/workspace-restic-v2/草稿PR#3（前置PR#2→PR#1，不合并）；用户docs32/docs34保持。上轮固定构建入口/实证/推送为进展，本轮新增独立workspace_deployment及初始化状态机、只读配置视图、Shell init-v2/status-v2和owned Go/Agent分支；不新增Agent工具或付费调用。
- 固定新私有部署根/home/hx/.local/share/sandboxd/deployments/entry-3c927792095347c18680af01f0e34d47，对应全新owned-deploy前缀/immutable Secret/controller/owner marker；原Go/Agent目录身份、密码、初始化job先持久化。PLANNED→SECRET_READY→INIT_SUBMITTED→OWNER_READY→READY，未知结果只查原Pod，缺失/失败/UID/spec变化不盲目init；证明先落盘再目录绑定/同UID清理。默认NFS运行、旧共享/污染仓库均保留。
- 实际init-v2、重复同目录init及status成功，原始-init-command/-reinit-command/-status保留；重复不产生第二个init Pod。真实统一Shell Go+Agent TCP/SIGKILL自动接续通过-deployment-entry.json：同Task原预算/时钟/deadline/provider不变、2attempt/不同Pod UID，4模型槽含在途预留、写文件仅1次、恢复读hash/原Session前缀正确，3个COMMITTED/revision1～3/同Owner/无pending。Agent环境Operator及合成存储canary均移除，未放入真实存储凭据。kill→API59,617.37ms、→核查89,464.54ms是单样本，含挂载盘导入/刻意等待/同步备份，不当生产RTO。
- 独立标准库部署审计-audit及-command已通过：Go32/CP10/Task35条checksum/seq、claim/原预算/父链/服务端文件hash一致；实际专属仓库marker+3份业务snapshot及正常check --read-data通过，业务/维护Pod0。没有重新运行任务或删snapshot，不是初始化进程故障实验、历史分支或完整十项验收。
- 本地初批19/19、1.697秒，最终相关23/23、2.642秒通过-initial-tests/-final-targeted-tests；初始化确认丢失、缺Pod、绑定中断、错误身份/失败/过期Lease/目录/锁等是Fake协议验证。完整Python332/332、320.665秒通过-full-python-tests，原生session95959实际退出0；未因记忆CLI导入等待而重启，旧306项不冒充本轮成绩，各批有重叠不相加。
- 入口原始报告保留执行当时Shell SHA；最终仅status-v2帮助文字一行有差异，按单行重构已核对Go/Agent执行分支未变，不为统一hash重跑服务。Go生产源码未改，当前新缓存二进制仍为4e7930a批次构建；新的目录/凭据/快照/全部日志保留。
- 收尾-final-checks实际通过：332项原文/部署原始日志hash一致，三项目容器running且非paused，业务/维护Pod0、8080/8090/10880/18090空闲，3个实际存储凭据字面/Base64在23份本轮公开文件零命中，用户两份文档hash保持。该检查点捕获当时文档hash；后续仅补本文最终回归记录和证据索引，不把动态文档hash当不可变源码证明。源码/原始报告最终固定值另在-final-v2-checks，避免文档自引用。
- **第一项下一步**：对文档43十项逐一审计现有真实报告和原始账本，重点核查V2-07缺失/校验/镜像拒绝、V2-08活动来源拒绝及V2-06同保存请求幂等的实际证据，缺项补独立真实服务验收。统一部署已有真实证据，不能再写成全部待实现；完整Goal保持active，不将任何可选夹具未运行自动扩大为新目标。新部署目录已有Task，日常启动可复用，entry合成夹具不复跑此目录。

### 2026-10-18：统一存储构建入口已真实验证

- 起点8727abc，codex/workspace-restic-v2/草稿PR#3实际OPEN、base=codex/workspace-nfs-v1，前置PR#2→PR#1；用户docs32/docs34 hash保持。上轮流量解释与只读检查未改变验收状态，本轮新增hack/build-workspace-storage.sh和workspace.sh build-storage，推进统一V2安装入口，不重跑已完成性能实验。
- 串行真实执行build-storage通过：resource available20,503MiB/swap0、项目单节点检查；固定restic0.18.1缓存校验、已有runtime-v1.5和restic-v3镜像复用（Docker ID未变）、当前Go服务及固定Pod渲染器重编译、kind加载通过。构建输入.cache/workspace-storage-build-3oNWBXPQ保留；默认.cache/workspace-sandboxd现为此批源码构建的新二进制，不再使用旧缓存。历史owned-cleanup可信缓存与其他镜像/证据均保留。
- 原始phase9-v2-unified-storage-build-command.txt、-build.json记录实际Docker/CRI身份、二进制hash与业务/维护Pod0；4/4标准库Fake命令契约测试通过-build-tests.txt，覆盖已有tag不覆盖/首次命令/编译失败保留旧服务/集群门禁。Fake首次路径不是无缓存真实Docker构建，本轮不启动Agent或初始化仓库、不新增存储故障、不调用外部模型；不能当统一V2启动验收。
- 两份用户文档hash已复核不变，原数据/卷/Secret/快照保留。部署README加入命令、前置条件和未接线边界；当前旧shell restic路径仍为v2，不因准备v3镜像就声称owned默认运行已切换。整体formalV2AcceptanceComplete保持false。
- **第一项下一步**：新私有部署目录中的fresh Owner/bootstrap持久状态机，固定Secret/prefix/init job，重启不盲目init或收编旧历史；绑定Python检查点目录和Go参数后真实执行统一Go/Agent restic入口。再按文档43全部十项逐项审计/相关回归及PR交付，Goal保持完整active。

### 2026-10-18：固定V1/V2后端性能与流量通过

- 起点fec103c，codex/workspace-restic-v2/草稿PR#3，用户docs32/docs34保留。上一轮真实GC/故障/恢复/审计及PR为进展；本轮新增workspace_checkpoint_benchmark、独立标准库审计和3项纯逻辑测试。复用seed20261018及few4×1KiB/many32×256B/partial8×4KiB、2次list预热、暖读、单并发，不清内核缓存，不并发项目测试/安装。
- phase9-v2-fixed-workload-v1.json与主JSON各9/9真实通过；同一Go二进制、NFS部署、固定数据/hash。V2每试验全新独立仓库/Owner/Secret/控制目录，18次实际备份、9次初版恢复全量hash验证；无模型调用或删除。V1复用Go进程，V2逐仓库启动Go，prepare/claim不作纯归因；批量后端备份不是默认Agent每工具组联合CP，receipt显式sessionBound=false。
- 首次备份RPC P50三场景6.664/8.040/7.822秒，后续7.092/7.879/7.680秒；含prepare/claim/hash核验/release的恢复P50约14.6/14.6/15.4秒。后续packed新增0/0/5446字节，少量数据不变也有MinIO约62KB RX和70KB TX、NFS元数据开销，不将容器流量当公网或纯PUT。原始资源/网络端点、压缩统计、owner基线与du表观仓库字节全部保留，未测峰值/对象请求数/生产QPS。
- 独立-fixed-workload-audit通过：两组固定数据、全部manifest/hash、324条Go日志、9个独立crypto、18个不同snapshot、阶段/恢复来源、计数差和分位数重算。性能结束后相关7/7、0.018秒纯逻辑回归通过-unit-tests；不把旧完整306项冒称本轮新运行。数据、快照、Secret及控制根保留。文档46 §7～8有方法、表格和限制，明确本轮读RPC变化不可直接归因备份提速。
- 收尾-fixed-workload-final-checks通过：项目三容器运行无暂停，业务/维护Pod0，10880/18090空闲；9个新仓库各保留owner+2业务snapshot。11个实际存储凭据字面/Base64在13份本轮公开文件零命中，220份证据hash逐项通过，AST/diff通过，用户docs32/docs34 hash与前批一致。整个V2完成标记仍false。
- 本批有效提交009e7f5；已完成的基准与独立审计原件保持，Go生产/可信缓存、依赖锁未改，未下载新包。不把后端batch RPC或容器端点统计当Agent端到端/峰值/公网账单；恢复当前工作从hack/workspace.sh的restic入口接线开始。
- **第一项下一步**：统一restic安装/启动入口接fresh Owner/bootstrap和可复现配置，保留旧共享数据不收编，再按文档43十项逐一核对原始证据/命令/回归并更新PR。性能目标已取得本机小规模实证，不重复试验筛数字、不扩大为生产HA；Goal保持完整active。

### 2026-10-18：新专属仓库真实联合GC通过

- 起点61bdb39，codex/workspace-restic-v2/草稿PR#3，用户docs32/docs34保留。上一轮Python管理协议/目录归属、本地回归和PR为进展；本轮新增workspace_gc_joint_demo及独立标准库审计，start_go管理夹具显式Owner/Operator参数，旧调用默认不变。新owned-joint/UUID仓库及controller/Secret/控制目录，完全不复用旧共享或故意污染夹具。
- phase9-v2-owned-gc-joint.json真实通过：Agent Replay经实际Go/runsc/NFS/restic/MinIO创建5份联合CP（5模型槽/4工具槽），head/pin/保留取消计划来源3类拒绝；新第1点在真实Wait/Go证明后实际管理员SIGKILL+Go SIGKILL，沿原GC/job/Pod UID仅Wait+Finish对账，无后端重跑。原Task已成功后才注入管理员故障，不宣称unfinished Agent跨Go故障自动接续。原Task/Session字节与deadline/calls不改，活动NFS仍version3。
- 新第3点实际排他check锁使清理Wait503/helper1，PREPARED冻结；有效另一工作区备份Reserve409。正常CONT/INT释放自己的锁后，固定snapshot显式attempt2/newjob成功，备份Reserve200；无force unlock，内部restic退出码未采集。实际prune后，从pin的第2点恢复新空NFS并经RPC读回version2/hash；这是保留文件恢复，不是新历史分支Task。只遗忘本次新第1/3点，COMMITTED历史和全部旧数据保留。
- 独立-owned-gc-joint-audit通过：CP30/Go85/Task42条checksum/序号、Session引用父链、claim及原文件hash；核对PREPARED→submitted→Go证明→FORGOTTEN→released顺序（时间规范为微秒并结合协议，不以墙钟代替因果）。实际仓库恰留owner marker+第2/4/5点4个snapshot，crypto/controller/专属前缀和正常read-data检查通过。初次系统Python不识别Go RFC3339Nano的Z/9位小数而失败，原-audit-command保留；显式兼容后-v2-command通过，没有重跑原实验或改日志。
- 真实checkpoint_admin preview通过-owned-gc-joint-preview及-preview-audit：10个控制/引用/claim文件hash/ctime/mode不变，5组均不可再清理，无pending；允许读atime变化。最终完整Python306/306、297.056秒通过-full-python-tests，Go生产代码未改不重跑旧Go。真实实验后available20,521MiB、swap0、业务/维护Pod空；服务关闭，所有剩余仓库数据/Secret/目录/日志保留，外部模型0。
- 收尾-owned-gc-joint-final-checks通过：AST、项目三容器健康/无暂停、Pod0/端口10880与18090空闲、MinIO200，4个实际凭据的字面/Base64在15份本轮公开文件零命中。213份证据hash逐项通过，进度矩阵仅V2-09按上述实证置verified，整体formalV2AcceptanceComplete仍false；其余项继续逐项审计，不用本轮成绩替代全部目标。
- 本批有效提交7d71580；实际控制根workspace-gc-joint/ad67caa100fd4ff78f830bac6e935ced、目录claim、Secret和余下4个snapshot保留，fixture默认每次新UUID，不用原目录重跑删除实验。可信Go缓存未替换，用户两份文档hash与前批一致。下一步直接从固定负载性能开始，不重复本轮已完成的清理实现。
- **第一项下一步**：固定同条件NFS V1与V2性能/流量、实际上传/去重/占用与恢复成本，再接统一restic启动（新Owner bootstrap，旧共享只保留），最后按文档43全部十项审核与PR交付。V2-09有完整真实候选证据，其他未审计项和性能不缩减；Goal保持active。文档48 §12为本批命令/结果/边界。

### 2026-10-18：独立Python管理协调器与目录归属

- 起点ed8f8f2，codex/workspace-restic-v2/草稿PR#3；用户docs32/docs34保留。上一轮Go持久GC/Operator及回归/PR为进展；本轮接同CP日志repository.binding和cleanup.execution、独立管理客户端/CLI及协调器，不向agentd服务或模型工具提供Operator身份。
- 绑定完整Owner及原生私有目录路径/设备/inode；固定本机repository-controllers/crypto.json独占登记，另一个控制目录/复制账本拒绝，已有CP历史和Session引用不收编。不是跨主机排他/HA，目录或claim损坏不强行修复。PENDING持久化GC/job后才Reserve，复核引用并PREPARED后Start；同owner/完整目标/Go持久证明核验后FORGOTTEN→Finish→DONE。失败保持冻结，显式retry先落盘新attempt再远端调用，响应丢失沿原nextJob对账。
- 初始18/18、2.696秒通过-cleanup-coordinator-initial-tests；随后相关34/34、6.818秒通过-final-targeted-tests。完整Python303/303、312.634秒通过-full-python-tests；最后补启动门/只读预览/异常关闭测试及client close嵌套finally，由最终-v2-targeted报告单独核验，不把303说成包含后补全部测试。CLI默认错误不打印HTTP异常链，客户端仅loopback/固定动作/有界响应、无重定向/环境代理/自动重试。
- 最终相关38/38、7.624秒通过phase9-v2-cleanup-coordinator-final-v2-targeted-tests.txt，含本批14项新测试及旧引用/保存协调器回归；303完整与38相关有重叠、不相加。完整进程一直等待原session结束，没有因观察超时重启。源代码/文档diff及AST检查通过，用户两份文档hash与前批一致。
- 有效实现78bde50；收尾-final-checks核对7份源代码hash、204份证据hash及既有可信Go缓存二进制hash一致，未启动该服务。无新依赖/下载、原始日志保留；当前真实联合GC仍未执行，不能把本批Fake控制结果当后端删除证明。
- 预览另列Owner/pendingExecutions，引用plan SHA原投影不变。维护PENDING阻止新CP/恢复，restic服务在Worker前拒绝启动以保留原Task/预算，Runner选模型/路由前再次预检。Fake切点明确是合成协议异常，不冒称实际SIGKILL/restic结果；未启动新服务、访问实际仓库或调用外部模型。
- **第一项下一步**：使用新干净专属仓库和.cache/workspace-sandboxd-owned-cleanup，真实走目录绑定→Session/文件联合CP→管理员引用复核→Go屏障/Operator→实际forget/prune→保留版本恢复；加入中断/锁冲突及原目录不变证据，不能复用旧共享或故意污染夹具。再完成固定性能/流量、统一V2启动和十项完整审计。文档48 §11为管理代码/命令入口，Goal保持完整active。

### 2026-10-18：持久GC执行与Operator入口本地闭环

- 起点a96a9bc，codex/workspace-restic-v2/草稿PR#3，用户docs32/docs34保留。上一轮持久仓库绑定、回归和PR推送属于进展；本轮复用维护Pod/WorkspaceBarrier新增forget/prune固定执行、全局互斥、结果证明落盘、显式同目标新job重试及Operator专属HTTP。关联ID不创建NFS目录，管理Pod仍无业务PVC，Agent无此API权限/模型工具。
- reserve只冻结；Wait核验真实Pod结果后fsync CleanupResult；Finish要求已有证明，可跨“删Pod后”窗口重启。未知/非零/超时仍冻结，retry须旧Pod确定停止并清理同UID；防止旧请求释放新job、历史gcId或旧jobId复用。尚未提交Pod且尚未写PREPARED时，管理员可cancel-reservation撤销过期计划；Go能证明never-submitted，上层未PREPARED条件须由后续可信协调器保证，不能把此接口当通用失败解冻。
- 本地新增Runtime7项/API3项顶层测试，覆盖持久冻结、正反互斥、坏结果、证明恢复、非法完整日志、历史ID、预留撤销和管理员权限。最终phase9-v2-cleanup-runtime-final-v4-race-tests.txt的sandbox/API通过，Go全项目-full-go-tests通过。final-v2/v3保留同一错误映射失败：旧未归属后端已拒绝但返回500，应409；修复预留前Owner检查后通过，没有实际删除或越权。初轮/targeted等仅对应当时代码，不冒称包含后补场景。
- 本批仅本地逻辑/fake Kubernetes，新服务0/实际仓库操作0/外部模型0；未改Python、不重跑旧292，原数据/镜像/仓库/快照/卷保留。available20,526MiB、swap0，未启动故障实验。文档48 §10记录固定接口、代码顺序、边界和后续验收；完整V2仍不宣称完成。
- 有效实现2a09349；新缓存.cache/workspace-sandboxd-owned-cleanup已构建但未启动，旧二进制保留，hash见-cleanup-runtime-final-checks。当前198份证据hash逐项通过；源码/文档diff检查通过。两份原始失败报告各保留一行空白尾空格，整体diff仅这2条已知告警，不改写原始日志以凑格式通过。用户docs32/docs34 hash与前批一致。
- **第一项下一步**：独立Python管理协调器绑定唯一控制目录，持锁核验引用、Go Reserve后复核并fsync PREPARED、固定Start/Wait、FORGOTTEN后Finish；崩溃从同GC/job对账，PREPARED后不撤销，锁冲突不强解。随后新专属干净仓库真实联合验收，不复用旧共享或故意污染夹具。性能流量、统一restic入口、十项最终审计仍保持完整待办。

### 2026-10-18：Go持久仓库绑定与owned备份接线

- 起点da46dcf，codex/workspace-restic-v2/草稿PR#3仍OPEN，base为codex/workspace-nfs-v1；保留用户docs32/docs34。上轮为流量答疑/源码检查，没有新增性能成绩；本轮新增实际代码和回归证据。Goal保持完整active。
- 新repository.binding只允许全新Go控制日志，fsync固定controller/repo crypto/owner marker、Secret名称和MinIO IP；重启相同配置幂等，拒绝换绑定、漏Owner降级及旧工作区自动收编。三项可信部署参数完整校验，BackupRuntime实际构造backup-owned并核对成功结果crypto，恢复来源错仓库在预留前拒绝。旧未绑定模式默认保留；没有给Agent新增执行参数或删除接口。
- 初轮3项顶层定向通过phase9-v2-repository-binding-tests.txt；后增配置及恢复来源重建测试，最终config/helper/sandbox/API race全部通过-final-race-tests，Go全项目通过-full-go-tests。均为本地逻辑/fake Kubernetes，无实际Pod或MinIO操作，不重跑Python、不把旧292当本批成绩。资源available20,557MiB/swap0/根盘930GiB。
- -audit记录8份代码hash及4份测试原文，当前进度188份证据hash逐项核验通过；初次证据收集器因Go ok后的空格格式误拒，修正解析而未改原测试或重跑。外部模型0、新服务0、删除0；原仓库/卷/快照保留。仓库绑定只约束该Go日志，不宣称已完成Python控制目录唯一归属或跨部署排他。
- **第一项下一步**：复用维护Pod生命周期补持久全局GC屏障与可信Operator专属入口，再完成Python控制目录归属/PREPARED→真实后端→FORGOTTEN协调器、新专属干净仓库的引用保护/中断/锁冲突验收。性能流量、统一V2入口、完整十项审计仍待完成。文档48 §9为本批代码阅读入口。

### 2026-10-18：owned helper与新专用仓库实际forget/prune组件通过

- 本批有效实现9d50421（起点d02c3fe），codex/workspace-restic-v2/草稿PR#3，用户docs32/docs34保留。上一轮本地引用/预览、292回归及PR为进展；本轮增加可信Go helper的仓库归属与固定init-owned/backup-owned/forget/prune契约，管理Pod不挂业务NFS，未开放Agent API/模型工具，默认运行仍为v2镜像。
- fresh init创建不可删除的owner marker snapshot，绑定controller UUID/repo crypto/marker完整ID；验证标记内容及全部快照归属，拒绝旧未标记/其他controller/歧义标签。forget只处理完整精确ID并核对剩余集合，缺失目标幂等确认；prune正常取restic锁、不unlock，操作后标记与快照集合不变。helper不是head/pin授权器，仍需控制层计划及管理员入口。
- 固定0.18.1资产构建新v3镜像并载入kind，旧v1/v2保留；Docker inspect Id086857...、实际CRI0253fd...分列，构建输入hash见-owned-image-build。Go相关初轮通过，新增歧义标签/无业务卷测试后最终race(helper/sandbox/API)和Go全项目通过；Python生产代码未改，不用旧292冒充新组件执行成绩。
- 资源available20,608MiB、swap0、根盘930GiB、业务/维护Pod空。phase9-v2-owned-cleanup-component.json真实通过：全新owned-cleanup/UUID前缀，16次实际runsc操作、4次预期拒绝（marker、错repo、错controller、未归属snapshot）；仅删除本次新建的第一个合成版本，重试/实际prune/read-data检查/剩余版本恢复均通过。业务写入经旧已验证Go文件RPC、源owner停止后再备份；独立-audit核对操作/集合/新前缀，不计另一实验。原所有仓库/卷/旧快照保留，最后故意未归属合成版本保留，不复用该污染夹具仓库作部署。外部模型0。
- 收尾-owned-final-checks核对语法/diff、业务/维护Pod及10880/18090清空，4个生成存储凭据在19份本轮文件无匹配；4次helper非零是预期拒绝，未发生未预期失败。构建缓存/旧镜像、新仓库owner-binding、Secret及所有剩余数据保留。
- **第一项下一步**：把该helper接入可信Go持久全局GC屏障与管理员专属入口、唯一控制目录绑定和PREPARED/FORGOTTEN协调器，再做引用保护/中断/锁冲突端到端真实验收；组件直接管理员Pod/主动停止源写入者，不是完整管理协议。性能流量、统一restic入口、十项最终审计仍待完成，Goal active。文档48 §8为本批续作入口。

### 2026-10-18：引用保护、只读预览与遗忘控制协议

- 本批有效实现12c8042（起点0a1d640），codex/workspace-restic-v2/草稿PR#3，用户docs32/docs34保留。上一轮真实部分失败、282项完整回归和PR为进展；本轮推进受控清理所需本地控制层。CheckpointLedger新增同JSONL的pin与forget事件，按crypto ID+完整snapshot聚合所有逻辑别名/检查点引用，保护分支头、保留恢复源和显式pin；保存/恢复/清理进行中、FAILED带data及坏Session引用阻止清理。
- 管理预览返回稳定内容SHA，prepare_forget重新核查同计划并fsync整组PREPARED；先停止文件恢复资格再允许未来后端执行，同ID幂等、改绑定/过期拒绝。未知结果保持PREPARED，重建后仍拒绝恢复/新保存；FORGOTTEN保留原COMMITTED历史及节点树，按旧node选恢复点过滤不可用项。complete_forget当前仅合成测试使用，没有HTTP/CLI apply、实际后端删除或prune。
- 新checkpoint_admin固定preview/pin/unpin；同目录agentd文件锁占用拒绝，不自动建目录、不调用外部服务。DurableJournal新增只读模式，不chmod/追加/修断尾，保持排他锁；写入默认语义保留。初批相关42/42、6.612秒通过，后补3项共享组/坏事件/未提交别名测试，最终完整Python292/292、335.307秒通过phase9-v2-retention-full-python-tests.txt；FORGOTTEN为合成控制验证，不是实际删除成绩。
- 实际retained-preview已通过：此前真实两分支控制目录的9份日志/引用文件hash、ctime、mode完全不变；8个快照组4组保护、4组仅本地引用候选，含恢复来源保护。是实际离线CLI与旧控制数据兼容证据，不是新的存储服务删除验收。收尾-final-checks再次核对9份文件不变、语法/diff、3个生成存储凭据在13份本轮文件无匹配。未启动新Pod，原目录/快照/卷/Secret和所有数据保留，外部模型0。文档48为当前清理续作入口。
- **第一项下一步**：可信Go固定forget/prune与跨维护操作串行屏障、管理协调器及新专用合成仓库真实清理/恢复/锁冲突验收；再做性能流量、统一restic入口与完整V2矩阵。不得拿控制层方法或eligible候选代替删除授权/后端证明。Goal active。

### 2026-10-18：真实restic部分不可读与孤立快照验证

- 本批有效实现29c6960（起点fc937a4），codex/workspace-restic-v2/草稿PR#3，用户docs32/docs34保留。上一轮真实锁实验/回归/提交/PR为进展；本轮补V2-05最后的部分文件失败。新PartialBackup管理夹具只触碰本次新UUID工作区合成report.md，复用固定镜像/Go/同步提交协议；新增固定partial-file模式，不扩大Agent工具权限。
- 运行前available20,623MiB、swap0、根盘930GiB、业务/维护Pod空，原三容器运行。初轮phase9-v2-storage-partial-file.json失败保留：后端卷chmod未证明维护客户端读失败，实际三次COMMITTED、Task succeeded，不能计部分失败；原权限/服务恢复，快照和控制日志全保留。修正经可信kind节点既有NFS挂载SETATTR，验证挂载/PodUID/合成hash；在helper进入当前cp的真实snapshots子命令后chmod，再从维护Pod确认报告Permission denied而另一文件可读，才放行MinIO。不是调整生产提交规则或放开业务Pod权限。
- 新目录-v2真实通过：内部restic退出3/helper1、实际runsc/v2镜像；第二点cp-625c... PREPARING→FAILED，无DATA_READY/恢复入口、原head/revision1不变；1attempt/2模型槽/3工具槽、无再次写/调模型。故障启动至failed6,858.55ms；观察exit3后、业务挂载释放前恢复原权限，独立150秒看门狗与finally后端兜底保留，MinIO恢复。旧快照dump报告与NFS hash一致；新孤立不完整快照8d8126...可读文件存在/报告缺失，明确保留不forget。
- -v2-audit核对原始Go/Python19/27/5条checksum/seq、原loop deadline/timing/calls和真实停止→释放，不是第三次实验。夹具初轮13/13与客户端可见性修正13/13均0.013秒；后加两个恢复兜底断言，最终完整Python282/282、290.392秒通过phase9-v2-storage-final-full-python-tests.txt。-final-checks确认两轮文件均600/UID65532、Pod/监听清空、MinIO正常、语法/diff与3个生成存储凭据在15份本轮文件无匹配。storage-failure-matrix汇总三个真实存储场景、排除首轮未触发不可读失败夹具，不计新实验/完整验收。文档47 §17记录命令、失败/修正、源码来源、角色权限与数据保留边界。
- **第一项下一步**：受控清理预览/引用保护/forget-prune，固定条件V1/V2性能流量、统一restic入口与完整十项证据审计/回归。三个真实存储异常分开保留；V2完整Goal active，不能因V2-05子场景齐备宣称整体完成。

### 2026-10-18：真实restic排他仓库锁冲突通过

- 本批有效实现214d1e5（起点640868a），当前codex/workspace-restic-v2/草稿PR#3，用户docs32/docs34保留。上一目标轮有真实MinIO超时、提交与PR证据，属于进展；本轮推进剩余真实锁场景。存储夹具增加固定repository-lock模式，默认minio保留；生产Runtime/Go/镜像未改，不增加Agent工具或自由restic入口。
- 新workspace_repository_lock管理夹具校验restic0.18.1二进制SHA、项目Secret/MinIO/loopback端口映射和来源crypto ID，真实check --read-data取得排他锁后只SIGSTOP自身进程；waitpid确认、独立150秒PID+starttime看门狗与finally正常CONT/INT退出，锁列表为空才算释放。桥接IP的管理侧无凭据健康探测不可达，改用同容器已发布localhost9002；不是修改业务NFS/MinIO架构。不会强制unlock或删除对象。
- 资源检查available20,629MiB、swap0、根盘930GiB，业务/维护Pod空，原三容器运行。phase9-v2-storage-repository-lock.json真实通过：原cp-45fa... COMMITTED/revision1保持，第二点cp-d264... PREPARING→FAILED；Wait503、真实restic退出11/helper退出1，实际runsc/v2镜像一致；锁仍活跃时核查原恢复头/Session引用/报告hash、1attempt/2模型槽/2工具槽。持锁后备份启动至failed4,473.75ms，不含管理取锁时间；随后正常释放自己的锁，目录/快照/Secret/控制日志保留，外部模型0。
- -audit报告校验三个原始日志19/24/5条checksum/seq，单次submitted及真实stopped证明→released顺序、原loop deadline/timing和calls保持；不是第二次服务实验。夹具8/8、0.005秒通过，最终相关协调器回归27/27、3.876秒通过-repository-lock-related-tests；未重跑旧267全量。-final-checks确认持锁进程退出、Pod/端口清空、MinIO正常、语法/diff与3个生成存储凭据在10份本轮文件无匹配。文档47 §16记录来源、命令、锁与Go屏障分工；失败实际发生在备份前快照查询阶段，不冒充上传中途失败。
- **第一项下一步**：真实部分文件备份失败；受控清理预览/引用保护/forget-prune；固定条件V1/V2性能流量、统一restic入口、完整十项证据审计及回归。V2-05已覆盖真实MinIO不可用/默认Wait超时和真实排他锁，不把尚未执行的部分失败当完成；Goal active。

### 2026-10-18：真实MinIO不可用与默认备份等待超时通过

- 本批有效实现fadd414（起点2244d86），当前codex/workspace-restic-v2/草稿PR#3 OPEN、base=codex/workspace-nfs-v1，远端与本地已核对；用户docs32/docs34保留。上一轮仅回答流量问题、未推进代码，本轮按未完成存储故障继续。新增管理夹具workspace_storage_fault_demo，先保存真实COMMITTED，再在第二点PREPARING后暂停专属MinIO；固定容器名/Compose标签/不可变ID校验，独立150秒看门狗和finally恢复，不改生产Runtime/Go、时钟、调用预算或权限。
- 运行前available20,647MiB、swap用量0、根盘930GiB、业务/维护Pod空；只有kind/NFS/专属MinIO运行。phase9-v2-storage-minio-outage.json真实通过：第二点wait默认90秒在90,001.83ms返回504，abort/finish200，暂停至failed93,483.74ms；原COMMITTED head/revision1保持、第二点FAILED、无pending、owner停止，2模型槽/2工具槽、1attempt、NFS报告hash一致；断言时MinIO仍paused，随后恢复健康HTTP200，Pod/端口清理，数据全部保留，外部模型0。
- 故障中-live报告核实维护实际runsc/v2镜像/UID65532；独立-audit报告核对Go/Python三个控制日志19/24/5条checksum/seq、PREPARING→DATA_READY→COMMITTED及下一点PREPARING→FAILED顺序、最终Task/calls、原loop deadline/timing与owner停止。只读审计不是第二次服务成绩；本批没有再次恢复旧快照内容。文档47 §15记录命令、范围及学习讲法。
- 本地夹具3/3、0.002秒与相关22/22、4.034秒通过；随后增强unpause后必须真实nonpaused才停止看门狗并增加第4项单测，最终23/23、3.988秒通过-final-related-tests。生产代码未变，不宣称旧267项覆盖新夹具。收尾-final-checks语法/diff、Pod/监听清理、MinIO状态通过；3个生成存储凭据在11份本轮文件无匹配。本批真实故障与仓库锁/部分文件失败/GC分开，V2-05只覆盖MinIO不可用及Wait超时，完整Goal active。
- **第一项下一步**：真实仓库锁与部分文件失败，受控清理预览/引用保护/forget-prune、固定条件V1/V2性能流量、统一restic入口及完整V2矩阵。新夹具保留原始失败资料、所有卷/快照/控制目录；不强制unlock、不删除旧对象。

### 2026-10-18：恢复窗口TCP故障与取消四场景通过

- 本批有效实现5f17541（起点b321274），当前codex/workspace-restic-v2/草稿PR#3，用户docs32/docs34保留。新增workspace_restore_fault_demo，仅自身子进程包装真实方法，固定started/files-ready/before-attached/attached/after-tool/cancel-ready；普通第二进程自动接续原Task，Go/storage不重启，默认旧进程入口不变。after-tool采用CAS写入与来源快照不同的新内容，验证最新文件和非零调用预算；取消用真实Start后的有界异步门，不伪造慢存储。
- 资源检查：可用内存20,628MiB，swap使用0，根盘931GiB，项目业务/维护Pod与10880/18090监听均空。真实files-ready已通过phase9-v2-restore-cut-files-ready.json：OS确认SIGSTOP再SIGKILL(-9)，普通Agent原目录进入attempt2，原Task/request/目标、claim时钟/限额保留，2模型槽/1只读工具、restore submitted一次、hash/完整配对正确。kill→ready60,576.28ms、→核查76,505.82ms；外部模型0。
- after-tool首轮原生Task实际succeeded、2attempt、原loop deadline/timing与已用调用逐项不变，但夹具把pre-loop claim deadline和loop deadline比较而AssertionError。原phase9-v2-restore-cut-after-tool.json及日志保留，Go/Pod正常清理；修正独立verify_phase_clocks并加单测，未改变Runtime/时钟/预算，新目录-v2重跑通过：旧loop起点/deadline/timing和旧调用逐项不变，2模型/1次CAS写，attempt2新业务UID不同、恢复submitted一次，NFS新hash81dafd...未退回来源；kill→ready57,122.93ms、→核查68,486.01ms。
- 本地夹具首轮21/21、3.807秒与下一轮22/22、3.531秒通过；时钟修复后完整267/267、298.700秒通过phase9-v2-restore-fault-latest-full-python-tests.txt；此前完整266/266、291.314秒保留，不冒称含后改动。文档47 §14记录命令/故障范围/失败原因。目录、快照、卷、Secret和原控制日志保留；Goal active。
- 真实cancel-ready首轮失败：API一度interrupted、Go清理503，停止未确认时未继续认领/模型；finally可信对账后Task cancelled、calls空、Pod清空，不把清理当验收通过。首轮唯一API原因未捕获；复核补强Go已停止维护Pod的finalizer CAS，重新核对固定UID/Spec/RV，最多3次；持续失败/UID替换保持冻结，阶段日志不含秘密。4个合成子场景及sandbox/API race通过；初版测试UID/string比较编译失败保留，修正后通过。新独立Go二进制.cache/workspace-sandboxd-restore-cleanup已构建，旧版本保留；cancel-ready-v2真实通过：HTTP取消后2,503.38ms，1attempt/calls空/无业务Pod，目标GoClaim409、默认原分支节点正确；本轮无CAS重试日志，不能归因首轮唯一原因。Go全项目通过。
- before-attached真实通过：Session已选旧节点但ATTACHED前强杀，普通Agent原目录attempt2完成绑定；restore只提交一次、状态/父链/hash一致，kill→ready54,866.48ms、→核查71,379.09ms。四场景严格原始日志审计采用phase9-v2-restore-fault-four-case-audit-v2.json；初稿无效节点比较被严格COMMITTED源节点相等验证替代，原件保留，不计第五次服务成绩。最终业务/维护Pod与端口均空，仅原三容器运行，数据全保留。
- **第一项下一步**：started/attached独立切点未执行；下一步推进存储异常/仓库锁、引用清理、性能和统一入口/完整矩阵。不得把已实现的六阶段都填成通过。


### 2026-10-18：Task/API历史分支接线与真实两分支通过

- 本批有效实现f0930f5（起点82a6b2d），当前codex/workspace-restic-v2/草稿PR#3，用户docs32/docs34未动。RestoreCoordinator/TaskStore/main新增明确checkpoint+UUIDv4请求接口及状态查询；旧node branch在restic模式找唯一COMMITTED版本，off/V1仍拒绝历史文件恢复。先固定来源引用和queued Task以接受取消，Worker再恢复/验证/FILES_READY/ATTACHED，之后才允许Runner认领业务Pod/调用模型。Session有未完成Task时拒绝切换路线。
- 同Task原claim/loop时钟和预算保留，恢复整段90秒且不超过原deadline；重启复用原job/目标、先核查旧执行者，未知停止阻止新attempt；ATTACHED后沿最新NFS继续，不重新restore覆盖新文件。来源已RESERVED但Task缺失启动补建原ID；后续状态缺原Task拒绝。取消和ATTACHED短锁排序，启动queued投影有旧活跃attempt时先显示cancelling，不提前终态。
- phase9-v2-restore-branches-component.json真实2场景通过（69,547.90ms）：实际Agent ASGI处理器/TaskStore/Runner→真实Go TCP/gVisor/NFS/restic/MinIO，从同一旧点创建两条独立路线；第一条新增branch-only文件，第二条目录及精确Session父链没有兄弟内容。原Task账本/来源分支head/原控制文件hash不变，首份parent=来源、revision1，最终head分别3/2；新模型槽3/2、工具槽2/1，全部固定Replay、外部模型0。维护实际runsc、业务镜像/gVisor启动与NFS hash核对；不是Agent TCP/SIGKILL/GC实验。
- 独立Go/Python原始控制日志checksum/seq及proof→release→claim顺序审计通过phase9-v2-restore-branches-audit.json，不计为第三个服务成绩。实验后两命名空间无Pod，10880/18090无监听，仅kind/NFS/专属MinIO运行；目录/快照/卷/Secret/日志保留。运行前内存可用20,696MiB、swap使用0、根盘931GiB。
- 初轮定向52/52、10.589秒及下一批55/55、11.551秒通过；后来增加节点歧义/最新文件恢复和启动取消缓存修复，完整Python258/258、290.382秒通过phase9-v2-restore-store-full-python-tests.txt（含当时9项恢复Task用例）；后加启动取消缓存修复与第10项用例由最新定向58/58、12.618秒覆盖phase9-v2-restore-store-latest-tests.txt，不冒称258包含后改动。统一入口restic维护镜像固定改为v2，shell语法与完整启动验收分开；细节见文档47 §13。
- 收尾补失败路由：未ATTACHED/失败恢复Task不成为默认resume工作区；默认上下文和Session查询按最后可用Task的持久完整节点选择，避免Session已选旧节点但ATTACHED前失败时把旧上下文与原分支最新文件混用。新增窗口测试最终59/59、12.697秒通过phase9-v2-restore-store-routing-tests.txt；最终完整Python260/260、288.855秒通过phase9-v2-restore-store-routing-full-python-tests.txt，覆盖最终代码。真实两分支报告不冒充此故障窗口的真实服务实验。
- **第一项下一步**：接真实恢复窗口取消/进程切点、存储异常/仓库锁，然后管理清理预览/引用保护/受控forget-prune、性能和统一入口/完整矩阵。历史正常分支已验证，完整Goal active。


### 2026-10-18：历史恢复来源与分支检查点关系控制模块

- 本批有效实现c20cb06（起点2836588），当前codex/workspace-restic-v2/草稿PR#3，用户docs32/docs34保留。本批新增同检查点JSONL的checkpoint.restore事件：RESERVED→FILES_READY→ATTACHED/FAILED，首次固定source和新branch/workspace/task，幂等与重建；不接远程动作或模型工具。
- 分支头和保留恢复请求来源形成protected_checkpoints投影，失败也保留来源；尚无解除引用/forget-prune/管理清理，不冒称已完成清理验收。只有ATTACHED建立分支来源，首份检查点parent指向来源、revision从1起，核对来源Session父链/目标workspace/task。协调器保存无新head时读取持久来源，不改Task预算。
- 首轮定向34/34、5.804秒通过phase9-v2-restore-reference-tests.txt；此后追加来源前移/非法完整日志等检查，最终35/35、6.079秒通过phase9-v2-restore-reference-final-tests.txt；完整Python249/249、289.899秒通过phase9-v2-restore-reference-full-python-tests.txt，已核对包含本批六项恢复控制测试；各批有重叠，不相加。全部为本地合成逻辑，不当真实Session分支或存储服务验收。 新解析器只读重建既有真实Agent切点控制日志通过phase9-v2-restore-reference-retained-compat.json：原COMMITTED点/分支head revision3一致，JSONL与Session引用字节不变；不是新增文件恢复。
- **第一项下一步**：历史恢复协调器/TaskStore/API接线，严格先来源引用→新空目标可信恢复→FILES_READY→Task绑定→ATTACHED→入队，重启按原请求对账。真实取消/存储故障、清理、性能和完整V2十项仍待完成；Goal active。


### 2026-10-18：恢复HTTP与Python客户端真实链路通过

- 本批有效实现44b18c1（起点531b358），当前codex/workspace-restic-v2，草稿PR#3仍OPEN且base=codex/workspace-nfs-v1；依赖PR#2→PR#1、不合并。用户docs32/docs34不修改、不暂存。
- 新受限restore GET与六种POST动作接入可信执行器；Agent Token、目标scope/完整来源固定、未知字段拒绝，客户端有界且不自动重试。不增加LLM工具或执行配置入口。finish先要求stopped，再核验真实结果，不将30秒独立清理窗口用于等待执行中的restore；取消请求context后的abort/fail单测通过。
- phase9-v2-restore-http-component.json真实两场景通过：Python客户端→实际Go HTTP→runsc/NFS/restic/MinIO。正常新空目录文件hash/manifest及原业务镜像匹配；错误期望repo写入前失败，目录仍为空、成功自报和claim拒绝、清理后目标仍隔离。实际401/400/409、重复finish均核查。不是Session历史分支、模型调用或真实连接中断实验。
- Go全项目通过；最后API/sandbox race通过phase9-v2-restore-http-final-v2-go-tests.txt，含最终断连context测试（早先full不冒称包含它）。Python客户端相关14/14、0.634秒通过；完整Python243/243、352.315秒通过phase9-v2-restore-http-full-python-tests.txt，包含本批客户端测试。证据见文档47 §11。外部模型0，原目录/卷/快照/Secret保留。
- **第一项下一步**：实现精确Session父链、持久历史恢复与活动来源引用保护，再做真实存储异常、清理及性能。当前文件恢复HTTP已通，V2十项完整验收仍未完成，Goal active。


### 2026-10-18：可信恢复执行器与真实组件通过

- 本批有效实现a1eaab9已推送至草稿PR#3（基线7f2f911，依赖PR#2→PR#1，不合并），用户docs32/docs34保留。复用BackupRuntime维护Pod/终止证明，新增ReserveRestore/StartRestore/FinishRestore；没有restore HTTP/模型工具。helper四参数restore绑定完整repo crypto ID，config匹配在写入前；旧三参数管理组件保留兼容。
- 增加持久核验证明：Wait结果核对后先verifyRestore fsync，stopped仍冻结；再清理Pod、completeRestore记录来源并放行。修复“清理Pod后、来源提交前”失去核验结果的窗口；已落盘证明允许缺Pod清理重试，无证明仍拒绝。不信调用方success自报，失败目标隔离保留。
- 新维护镜像restic0.18.1-v2已从缓存固定基础/已校验restic构建并载入kind，v1与业务runtime-v1.5保留。Docker inspect Id=sha256:0d07dc6898e243106416570c005bd094ef86dec618cd57ef2c25677351468705，实际CRI imageID=sha256:146dcc0f65c9a1c7839494f74b7724f005a493974d27a9d41eff19759856ce58，类型分别记录，见文档47 §10。
- 真实phase9-v2-restore-runtime-component.json通过：从此前真实合成COMMITTED快照恢复到新空NFS目录，manifest/完整ID/业务imageID核对；新业务Pod读报告与NFS服务端hash一致，维护/业务CRI均runsc。错误期望仓库ID在写入前拒绝，目标仍为空、claim拒绝，清理后隔离保留。不是Secret轮换/历史会话分支/真实模型实验。
- 原控制目录-reconcile已验证幂等，日志hash不变，report与audit单列，不记作新故障成绩。对账入口首次编译误用List、修正为Records后构建通过。新组件失败输出仅记录stage，不输出raw Secret/CRI env；仅操作自己保留的native UUID目录。
- 新Runtime4项顶层（含三个身份不匹配子场景）、helper契约1项及原控制模块回归，helper/sandbox/API初始及最终race通过，Go全项目最终phase9-v2-restore-runtime-final-full-go-tests.txt通过。本批Python生产代码未改，未重跑全量，不冒称旧239项覆盖本批。资源可用内存20,680MiB、swap使用0、根盘931GiB；实验后两项目namespace无Pod，仅kind/NFS/专属MinIO运行，所有目录/快照/卷/Secret/日志保留，外部模型0。
- **第一项下一步**：受限restore HTTP/客户端，并接Session精确父链与历史分支管理；完整COMMITTED来源、目标新branch/workspace、原Task调用审计/预算/取消不可回滚，活动恢复源先做引用保护。然后真实存储异常/仓库锁、受控forget/prune与性能。当前仅可信文件恢复组件通过，Python会话分支仍未实现；Goal active。

### 2026-10-18：六个提交切点已验证；恢复目标控制模块已测试

- 本批有效实现cb4ce37已推送至草稿PR#3（起点c70251d，依赖PR#2→PR#1，不合并），用户docs32/docs34未暂存。已核对项目仅kind/NFS/专属MinIO，业务/维护Pod空闲，可用内存约20.7GiB/swap用量0/根盘931GiB。
- preparing和before-commit两轮真实SIGKILL均通过，原始报告phase9-v2-agent-cut-preparing.json、-before-commit.json；前者PREPARING→FAILED，后者DATA_READY→FAILED，最终head均revision2；原Task/预算/截止时间、文件hash及工具配对继续核验。没有更换Go服务二进制。
- 审计发现仍需“备份已成功、DATA_READY未写入”的即时切点，新增before-data-ready管理夹具包装及测试，已真实通过phase9-v2-agent-cut-before-data-ready.json：备份结果完整但Python点仍为PREPARING/data=null；重启后FAILED、最终revision2，kill→API ready54,865.64ms、→核查72,153.67ms。父进程仅通过原job Wait只读核对实际备份结果，不启动新备份，不将快照自动当成恢复点；外部模型0。不能因为其他五个切点通过就提前勾选完整协议。
- 新Go RestoreSource/目标隔离/恢复来源固定逻辑：专用新空目标的metadata预留、失败未核验目标禁止认领、全局维护可释放给其他工作区；成功路径先写restoredFrom再释放verified屏障，中间重开仍冻结；认领固定业务imageRef/实际imageID。保留旧零值JSON格式与旧日志读取，不修改旧目录。尚未接可信restic恢复执行器/HTTP/分支，纯逻辑测试不冒称真实恢复。
- sandbox/API本地及race通过，最终追加来源发布中间窗口后race再次通过；Go全项目通过phase9-v2-restore-control-full-go-tests.txt。新模块5项测试为合成身份/状态机；既往Python239项不冒充本批夹具更改覆盖，最新Python定向19/19、3.617s通过phase9-v2-joint-six-cut-tests.txt。文档47 §9记录能力边界。
- 六个实际切点报告及预算/调用/恢复头只读复核汇总phase9-v2-joint-six-cut-summary.json已保存，所有报告通过，业务/维护Pod与10880/18090监听均为空，仅kind/NFS/专属MinIO保留。**第一项下一步**：接可信restore执行器；必须在写入前核对仓库crypto身份、实际空目标，成功后核对snapshot/manifest和业务镜像，失败目标保持隔离。再接Session精确分支/引用保护、真实取消/存储故障和同条件性能。完整Goal active。

### 2026-10-18：联合检查点进程切点（3个真实切点已验证）

- 本批有效实现9a6e519（起点56285fc，推送至草稿PR#3，依赖PR#2→PR#1，不合并）；新增workspace_checkpoint_fault_demo及测试，复用workspace_process_demo.start_agent默认V1行为，显式restic模式才使用切点子进程。仅管理夹具包裹真实方法，不添加生产故障开关、不改账本/时钟、不使用外部模型。
- 切点固定为reserved、preparing、data-ready、before-commit、committed；标记文件fsync/原子发布后SIGSTOP，父进程waitpid确认再SIGKILL(-9)，第二进程普通create_app原目录自动接续。Go不重启，本批不是Go崩溃或历史快照恢复证据。
- 首个真实data-ready通过：phase9-v2-agent-cut-data-ready.json；原点DATA_READY拒绝恢复，重启后FAILED且原snapshot身份保留，最终head revision2；同Task/会话/分支/工作区、原deadline/limits/timing/provider不变，原调用表逐项保留、写/读各1次，2attempt旧owner均停止，Session前缀与NFS hash一致。实际维护CRI runsc；kill→API ready57,013.97ms、→最终核查73,746.66ms，含挂载盘启动/清理，不是生产RTO。
- 首次夹具单测因Mock PID不是整数而打印JSON失败，原始29项1error保留；修正后29/29、7.051s通过。随后增加reserved测试与完整回归，结果单列，不能将旧29项冒称覆盖新用例。
- committed已通过（原COMMITTED保留，最终revision3，kill→ready50,588.48ms、→核查66,022.02ms）；reserved-v2也通过（原Go屏障存在/Python记录始终无伪造，最终revision2，kill→ready55,176.11ms、→核查72,445.33ms）。首轮reserved仅端口预检EADDRINUSE即终止，未创建实验Task；当时唯一根因未证实，后续无监听者，probe支持REUSEADDR并用实际监听拒绝测试验证；原失败输出和preflight整理保留，新路径重跑。执行前资源可用内存约20,714MiB、swap用量0、根盘931GiB，项目业务/维护Pod空闲。当前运行时不覆盖输出、不重复启动；进程是否仍活由实际执行句柄/系统进程判断，不能凭本文判定停止。
- 本批完整239/239、282.089s通过phase9-v2-joint-fault-full-tests.txt；端口probe后改动最终5/5、0.068s覆盖，中间4项报告保留，不冒称239项包含后改动。三个raw报告均有独立hash/一致性审计。业务/维护Pod已清理，仅kind/NFS/专属MinIO保留，工作区/快照/PV/PVC/控制日志/Secret未删，外部模型0。**第一项下一步**：补preparing/before-commit真实切点；再推进真实取消/MinIO故障、可信restore新空目录与镜像身份、历史分支、引用清理和同条件性能。V2十项完整验收尚未完成，Goal active；用户docs32/docs34不纳入。

### 2026-10-18：Agent联合保存接线与最终诊断恢复

- 本批实现fb426a7已提交并推送（前置有效HEAD d85aa2d），当前仍为codex/workspace-restic-v2/草稿PR#3，base为未合并V1 PR#2；后者依赖PR#1。用户docs32/docs34原有修改保留，不暂存。
- 新CheckpointCoordinator按Reserve→Session引用/PREPARING→Start/Wait→DATA_READY→核对Go停止屏障/owner→短锁COMMITTED→Finish执行；Runner/Loop只挂完整工具组和已持久化最终诊断，初始化告警不备份。设置AGENTD_CHECKPOINT_MODE=restic选择，默认off；禁用后端在claim/模型调用前失败。main生命周期关闭检查点账本；统一shell入口已加模式和配置检查，但restic shell分支尚未整套执行。
- 最终公开诊断独立于模型原文，保存在Runtime来源元数据+原Task ID的节点。Provider伪造标记被剥离；同Task最终备份中断接续可不重调模型，手动新Task仍真实进入Loop。恢复时及备份后复核60秒状态时效，过期追加历史诊断，最多一次额外检查点，原预算/deadline不变。独立128Ki字符结构化诊断持久化预算避免半个JSON，不扩大模型上下文或Session引用上限。
- 最终备份窗口已接受的steer/follow-up与成功提交在同一接受锁排序；有未注入命令就同Task新attempt接续，不吞命令、不重放写入。仍受原额度、deadline及恢复次数上限，取消优先。
- 三轮真实正常路径均成功：phase9-v2-agent-joint-checkpoint.json、-v2.json、-v3.json；最后一轮27,881.23ms，实际TaskStore/Runner/Go HTTP/gVisor/NFS/restic/专属MinIO，3个COMMITTED点、完整Session引用/父链/revision/snapshot及文件hash一致，3模型槽/2工具槽，维护Pod实际CRI均runsc；外部模型0。前两轮对应先前代码，第三轮覆盖最终时效修复。业务/维护Pod经Go清理，原生控制目录、工作区、PV/PVC、snapshot和Secret保留。
- 回归分批：最初完整223/223；随后完整231/231、288.359s通过（启动后增加的预检/诊断预算/时效不冒称由它覆盖）；最新定向52/52、9.409s通过，含恢复过期、备份等待过期、Provider标记、手动新Task、late follow-up和大诊断。首次late-control命令误写不存在的test_runtime模块，原始FAILED留存；修正后33/33通过。最终代码完整回归235/235、295.378s通过，原文phase9-v2-agent-checkpoint-latest-full-tests.txt；本批还检查Python编译、shell语法、9份Markdown链接/围栏及diff，最终测试文件仅去掉末尾多余空行，无逻辑改动。
- 环境再核对：Docker仅kind控制节点、项目NFS、专属MinIO运行；可用内存20,536MiB，swap总6,144MiB/已用0，Linux根盘约931GiB可用。NFS/MinIO地址仍172.23.0.3/.4，仅本次观察，后续重新核对。真实夹具使用.cache/workspace-sandboxd-v2，不覆盖旧Go二进制；使用既有专用仓库Secret名称，不输出凭据。
- 流量分析写入文档46 §6：manifest前后各全读一次，restic去重不消除源扫描；当前配额64MiB/512项，单文件256KiB。本机容器网络不等于公网流量。V2同条件流量/性能尚未实测，不宣称提速。
- **第一项下一步**：串行补真实V2进程切点/取消/MinIO故障及原预算对账；再实现可信restore屏障、新空NFS工作区与不可变镜像校验、精确历史分支、引用保护/清理预览/受控forget-prune，最后同条件V1/V2性能与十项完整验收。正常联合保存和Fake故障测试不能替代这些证据；Goal保持active。

以下为历史批次恢复记录，遇到状态冲突以上述最新批次、实际代码和原始报告为准。

- 当前有效实现提交c0697af（PR#3，承接8c1b967）。新增受限备份HTTP与Python客户端：Go启动三项完整配置才启用，默认V1关闭；Agent Token保护、Operator拒绝，动作仅reserve/start/wait/abort/finish，不接受镜像/仓库/argv。wait超时不清屏障，abort/finish独立清理；Python有界参数/结果且不自动重试，不注册模型工具。
- 真实phase9-v2-backup-http-component.json通过，采用实际Server loopback HTTP＋Go可信执行器＋gVisor/NFS/restic/MinIO；注册表重开后Files/Claim仍拒绝，显式Finish后内容/hash一致，清理前actualRuntimeType=io.containerd.runsc.v1。Go API/配置/Runtime最终race与Go全项目通过；Python客户端/远程文件/检查点26/26、1.948秒（MockTransport）通过。不是Python Agent联合检查点或SIGKILL验收；原工作区/快照/Secret保留，模型调用0。

- 当前有效实现提交6c14c68（PR#3，承接e8443ff）。新增CheckpointLedger与不可变SessionReferences：所选完整父链复制/hash/私有权限，PREPARING→DATA_READY→COMMITTED/FAILED，单条COMMITTED投影head/revision，幂等与边界ID查询、断尾/坏行检查。RecoveryLedger.commit_checkpoint与cancel共用短锁，核对原Task截止时间、节点、attempt和Go owner/generation/image，不修改预算。初始12项本地测试通过；完整Python214/214、285.135秒通过phase9-v2-checkpoint-full-regression-tests.txt，含线程取消/提交顺序与兄弟分支检查。随后镜像格式/作用域硬化由最终34项定向（2.140秒）覆盖，phase9-v2-checkpoint-final-boundary-tests.txt；不将214冒称包含后改动或把测试数相加。
- 引用发布最终修正：初稿link发布在崩溃后可能残留第二个硬链接导致拒读，改为单写入者下fsync临时文件→原子rename→fsync目录；目标已存在只验证，不用副本掩盖损坏。新增发布/目录fsync中断重试测试与相关回归35/35、2.401秒通过phase9-v2-checkpoint-publication-final-tests.txt；先前214/34批次不冒称含此后改动。
- Go BackupRuntime新增Reserve：只fsync冻结、不创建Pod，再由后续协调器保存Session引用/PREPARING；sandbox/API race通过phase9-v2-checkpoint-reserve-go-tests.txt。新检查点模块尚未接HTTP/Runner，所有十项V2正式验收仍pending；本批未新增真实存储/外部模型调用。

- 当前有效实现提交1448c82（V2草稿PR#3；以下说明承接e74aacc）。接入BackupRuntime可信备份执行器：维护配置持久固定，submitted先落盘、精确Pod/UID/spec/finalizer核对、终止+节点健康证明、Wait保留屏障、Abort有界、Finish幂等；源备份仍只读，尚未接restore/HTTP/Agent。执行器7项顶层逻辑测试（含子用例）、六包race及Go全项目通过；最后Finish幂等改动由sandbox/API race单列覆盖，不冒称先前全量包含后改动。
- 实际维护执行器探测：首轮备份成功但即时读取失败，原失败与同job后续对账保留；第二轮通过但事后CRI已回收；第三轮phase9-v2-executor-component-v3.json整体成功且清理前实际runtimeType=io.containerd.runsc.v1。注册表重开后写/认领仍拒绝、等待结果后仍冻结、显式Finish后内容/hash一致，业务owner和维护Pod已正常清理。不是进程SIGKILL或Session联合提交；原数据/快照/Secret/卷保留，外部模型0。

- V2分支已从9c91c9b创建，90d9c22已提交并推送、草稿[PR #3](https://github.com/chx739/sandboxd/pull/3)已创建并附到当前聊天；base=codex/workspace-nfs-v1，依赖PR#2→PR#1，不合并。该批模块：固定restic安装、只读manifest、受限backuphelper/CLI、gVisor维护Pod构造/定向MinIO网络策略、独立MinIO Compose与组件夹具。学习/后续持久屏障约束见文档47；尚未接Agent自动检查点，不将内存mutex当崩溃后屏障。
- restic0.18.1官方资产约10.14MB下载并校验，phase9-v2-restic-tool.json；新sandboxd-restic:0.18.1-v1已构建/载入kind，不覆盖业务runtime-v1.5。相关Go/race通过phase9-v2-backup-foundation-tests.txt。专属MinIO项目sandboxd-workspace-backup已启动，localhost9002，独立卷/私有本机凭据，不触碰RAG MinIO。当前是隔离本机HTTP，TLS未测。
- 三轮真实组件探测均已终止：首轮缺TypeMeta；第二轮备份/首恢复实际成功但夹具读取省略的readOnly=false字段失败，两份失败报告保留；修复后phase9-v2-backup-component-v3.json整体通过，8个操作均真实runsc，两个文件版本恢复至两个新空目录、重复后端请求复用snapshot。业务/维护Pod已清理，源目录/快照/Secret/卷保留。后端通过不等于V2联合检查点完成，十项正式验收仍pending。
- 本轮新增Go持久屏障模块：workspace.barrier与owner共用Go独占JSONL，reserved→submitted→identified→stopped→released，Files/Claim在重启后仍拒绝；提交结果不明、错误UID/旧CAS不能解除，停止业务owner仍可执行。8项逻辑测试与六包race通过phase9-v2-barrier-regression-tests.txt；全项目Go测试通过phase9-v2-go-full-regression-tests.txt；尚未接维护执行器，不把逻辑测试称为真实屏障验收。旧V1日志兼容；出现V2事件的控制目录不能再交给旧V1二进制写入，旧解析器将拒绝未知事件。
- 规格：[43](43-工作区持久化与恢复实施约束-NFS-v1与restic-MinIO-v2.md)；完整目标：[44](44-工作区恢复目标模式Prompt.md)。先 V1 最新状态、再 V2 restic/MinIO 历史快照，不改 NFS 运行主路径。
- 已检查：PR #1 为 OPEN，HEAD=281111d，base=main。新建 codex/workspace-nfs-v1；用户原有 docs32/docs34 修改仍未暂存、不纳入新提交。
- 本轮环境与旧快照不同：Ubuntu 22.04.5、可用内存约 22 GiB、swap=0、Linux 根盘约 935 GiB 可用。Python3.12/uv 存在；系统 Go 为 1.25.1，项目要求 1.26.5，需使用隔离工具链与缓存。
- Docker Desktop 已正常启动，Server29.8.0；旧容器/卷保留。本项目单节点 kind-sandboxd 已创建，Calico Ready、gVisor smoke通过；固定工具 Go1.26.5/kind0.31.0/runsc20260810.0 已下载并校验。
- 新增 DurableJournal 与 RecoveryLedger：进程锁、fsync/断尾/坏行处理、身份/预算/截止时间/取消/命令游标，20/20 本地测试通过。尚未接 TaskStore；不能声称重启自动接续完成。
- 新增 Go 固定文件 helper 与专属 Pod/PV/PVC 构造：路径/symlink/特殊文件/CAS/额度边界，helper 9 顶层测试及 race 通过；sandbox 定向回归通过。尚未接公开文件 RPC，默认文件工具仍是旧后端。
- NFS 配置在 deploy/workspace；用户已明确允许仅给可信服务容器增加 DAC_READ_SEARCH，不放宽业务 Pod。官方 Google Registry 连接超时，已从固定 Debian 基础摘要与 Ganesha4.3-2 包构建，单独服务172.23.0.3。导出父目录0711、工作区0700、root_squash匿名映射65532；真实非 root gVisor/NFS/helper读写及服务端hash一致通过，属于组件探测，不能冒充 Agent/自动恢复验收。
- 学习文档：[45](45-持久工作区与任务恢复学习手册.md)；逐项状态与原始证据：[Phase9](evidence/phase9-workspace-recovery.md)。初期正式验收未执行；当前V1逐项进展见下文，V2未开始，外部模型调用0。
- 第二批：Go独立JSONL与WorkspaceRegistry、固定workspace-init、工作区通用exec/释放拒绝与预热池隔离已实现，相关Go/race通过；仍未接真实生命周期和受限RPC。必须由Go采集真实容器终止证明后才能ConfirmStopped，不能把状态机方法当fencing。
- 完整Python离线回归110/110通过，295.970秒，原始输出 phase9-python-regression-native-tests.txt。第一次受限执行器因asyncio.to_thread线程唤醒无法退出而主动中断，部分输出phase9-python-regression-tests.txt保留为环境问题，非通过成绩；不改应用逻辑规避。
- Git交付：第一批dbc02d2已推送，草稿 [PR #2](https://github.com/chx739/sandboxd/pull/2) base=codex/agent-memory-rag-plan，依赖PR#1，已附到当前聊天；不合并。用户docs32/docs34原修改仍保留。
- 2026-10-18续作：新增WorkspaceRuntime，真实维护Pod初始化/NFS读写检查/Pod UID绑定/终止状态+NodeReady+近期Lease/finalizer释放；GoHTTP工作区接口和启动配置已接入。真实Go组件与HTTP组件各通过一轮：新attempt/Pod UID/代次变化、同workspace文件hash一致、旧attempt拒绝、Go账本关闭重开，见phase9-go-runtime-component.json与phase9-go-http-component.json；不是Python自动恢复成绩。
- 新Python RemoteFileWorkspace与Client/Plugin适配器已接，未知写入不重试，Loop退出当前attempt；定向17/17通过，phase9-remote-files-tests-final.txt。新增后完整Python118/118通过，276.228秒，phase9-python-remote-regression-tests.txt；默认Runner仍尚未选择Remote。
- 第三批d7bef93已推送至草稿PR#2。后续接口复查补缺失/null/空文本与UTF-8区分、存储执行错误通道、生命周期锁等待可取消；runtime-v1.1和runtime-v1.2分别以新tag保留镜像，正常真实HTTP组件复验通过。错误分类与取消等待为单测，未伪称已注入真实fsync故障。
- 后续785553f已推送，远端PR#2仍为OPEN/DRAFT、base=codex/agent-memory-rag-plan。新增恢复时钟schema2：排队未启动不计claim，首次attempt固定claim开始，首次绑定后固定loop开始；接续继承原阶段剩余时间，旧schema1不改写或补发额度。启动records扫描包含取消/过期待清理owner，数字开头的真实Pod UID可保存，已停止attempt和绑定不可改写。
- Session新增for_attempt视图：同文件/同锁，按task+attempt定位增量；旧半组工具调用仅留审计，新attempt从当前完整head继承，旧attempt不能追加/重新激活；返回fsync后的安全nodeId。旧线性迁移保留legacy归属，原字节不变。相关定向50/50通过，1.537秒，phase9-attempt-clock-final-tests.txt；只是模块验证，TaskStore/Runner尚未选择这些接口，不是V1自动接续验收。
- 本轮完整离线回归批次137/137通过，270.335秒，phase9-python-attempt-clock-regression-tests.txt；启动后追加的时钟回拨/新绑定前禁止调用/legacy线性视图边界由最终50项定向覆盖，不将137项冒称包含后来追加的全部用例，两份测试覆盖有重叠、不相加。compileall与diff检查通过。未启动新真实服务或调用外部模型。
- 2026-10-18 M2接线：TaskStore默认部署路径改为持久账本，启动扫描未完成Task、同Task新增attempt、原预算/时钟、取消先落盘、控制命令ID及消费游标、独立attempt.trace；Runner实际选择RemoteFileWorkspace并先对账旧Go owner。模型配置/Replay文件hash固定，游标计入不明预留；不明写入先读hash，不明Plan或无法确认的新内容停止。CLI默认nfs，local仅旧离线兼容；旧Session读取保留，旧文件迁移入口仍待做。
- 新Runner定向48/48、TaskStore/API/Session定向32/32通过；完整Python批次155/155、276.596秒。后续配置/路由/预算及数字runtime ID修复定向68/68、2.822秒，HTTP409/Trace鉴权及ID最终15/15、0.797秒；测试有重叠不相加，完整155项不是后续全部新增用例的完整重跑。
- 最后补取消与stale done运行句柄的竞态：blocked任务收到取消时仍排队对账原owner，不新增attempt/写入。TaskStore/API最终9/9、1.151秒通过，phase9-durable-cancel-final-tests.txt；compileall、历史演示bash语法、diff检查通过。旧run-agent-demo显式local，NFS验收走新的真实夹具；用户docs32/docs34修改仍未暂存。
- 真实V1-01通过：phase9-agent-workspace-smoke-v2.json，16.488秒；真实Go服务、gVisor启动、非root65532、实际NFSv4.1挂载，Agent写读report.md与服务端hash一致，正常释放Pod后文件保留。首轮发现Go数字开头sandbox_id被Python逻辑ID校验误拒；失败原文及原Go/Python账本对账释放证据保留，未强删Pod。
- V1-02首轮Task已自动两attempt成功，但夹具即时检查API对象消失过早而失败；raw与post-failure-control-journal-inspection保留，不能当正式通过。修正为有界等自然删除后，phase9-agent-pod-recovery-v2.json严格复验通过，19.886秒：完整工具边界后管理员正常删除Pod，原Task/session/branch/workspace不变，2个attempt/不同Pod UID/generation2，文件hash一致，4个模型槽/3个工具槽保留，旧/新owner均确认停止；自然删除观察等待258.69ms。不是强杀agentd、节点失联或模型质量测评。
- 2026-10-18真实进程验收：phase9-agent-process-recovery.json通过V1-03/V1-09。确实SIGKILL自建agentd子进程(returnCode=-9)，同控制目录第二进程自动接续原Task，未调用resume；Task/session/branch/workspace/deadline/limits/timing保持，旧模型预留转uncertain且不归零，写入仅1次，当前父链Tool消息配对、旧节点读200/携带工作区分支409。原/新Pod UID不同，Go旧释放证明和NFS hash保留。单轮kill→API ready54.688秒、→替换Pod观察56.925秒、→Task成功65.497秒，包含源码挂载盘启动和8秒Replay等待，不是生产RTO。
- V1-05：phase9-agent-terminal-restart.json实际创建succeeded/failed/limit_exceeded/运行中cancelled四类Task，再启动第五个服务；Task/Session/Go账本字节保持、不发新调用。失败用刻意损坏的Replay响应产生，超限由6轮真实工具循环产生，属于真实服务+Replay，不是手工写终态。
- V1-07：phase9-agent-write-ack-recovery.json通过，23.524秒。实际Go/gVisor/NFS写入后，夹具客户端丢弃确认；第二attempt实际读hash后接续，工具write_file只预留/执行1次，旧半组写入意图不进入可见父链；不是TCP断包实验。
- V1-08/V1-12：phase9-agent-interruption-cleanup-audit.json从此前真实报告和原始Session/控制日志复查。模型/工具中断仅恢复完整父链、原模型/工具槽不归零；正常/取消/失败/超限均保留0700 NFS目录、Retain PV/PVC及控制日志，目标Pod均消失。审计只读，不重放工具或伪造故障。
- V1-13：四个原有项目服务经标签/版本/资源/localhost端口核对后临时启动，ES未启动；真实Milvus2.5.10/OpenSearch+缓存E5/BGE三类联合回放3/3，phase9-joint-ops-replay.json。模型/Jev/沙箱生命周期仍为脚本/Fake，非真实模型能力；完整Python165/165、290.536秒，phase9-python-process-regression-tests.txt；延迟/只读日志和恢复定向16/16、0.995秒。回放后四服务停回原状态，旧索引/卷保留。
- 2026-10-18预算验收：V1-04两组实际SIGKILL/同目录重启通过。phase9-agent-budget-restart.json在第6个模型槽预留后强杀，重启前后6模型/5工具槽、原deadline/timing/provider不变，额度耗尽即停止，无新Pod；phase9-agent-expired-restart.json按真实墙钟等待原120秒loop过期才启动，仍2模型/1工具槽、仅原attempt，清理旧owner后limit_exceeded。无手动resume/新调用，不通过修改账本或缩短时钟造场景。
- V1-10首两轮发现真实缺陷：叶子链接写入被误作创建、父目录O_DIRECTORY|O_NOFOLLOW仍跟随链接。前三轮失败报告及原始输出全部保留；openat2候选在当前gVisor返回ENOSYS，管理员固定探针与精确Go清理证明保留。最终使用标准库O_PATH|O_NOFOLLOW先取得描述符、fstat确认目录，再从该已打开目录取"."；不是路径检查后重新打开，不新增依赖/权限。
- V1-10第四轮phase9-workspace-file-boundary-v4.json通过：最新runtime-v1.5、真实Go/gVisor/NFS、绕过Python Policy，20项越权/路径/symlink/大小/CAS拒绝，正确CAS对照成功，两工作区guard hash与链接本身保持，两个owner正常释放。旧runtime-v1～v1.4及工作区/卷保留；当前固定镜像runtime-v1.5，Docker inspect Id=sha256:8e1abdb6e17c11c98fb04f7ad707d748a35c199c9fc80fabbec300a60c7fb465，实际CRI imageID=sha256:ee4b19828a03a59cb10941cd1aa03e8829e47c82466e3f1977bfd001b5fdd1d1，二者类型不同。
- 本批次Go相关race与全仓库回归通过，最终phase9-directory-descriptor-tests.txt及phase9-go-directory-descriptor-regression-tests.txt；Python夹具/原时钟/Replay定向22/22、0.699秒，phase9-budget-boundary-final-regression-tests.txt。旧165全量为此前批次，未冒称本批次新全量；用户docs32/docs34未纳入。当前交付仍为PR#2依赖PR#1，不合并；本批次以前有效HEAD=1178856。
- 最新runtime-v1.5真实Agent文件工具及完整轮次Pod接管复验通过，phase9-agent-pod-descriptor-recovery.json，23.491742秒；模型4槽/工具3槽、原Task/workspace与hash保持，两个新旧Pod正常释放。没有把单轮当生产RTO。
- V1-06真实第二轮phase9-agent-concurrency-v2.json通过：第二agentd改用18091仍因JournalInUse退出，不是端口冲突；SIGKILL后自动接管原Task，旧attempt release409且新Pod仍读原hash；两个并发resume均409、两个cancel均202但只取消一次。另一个Task在Go starting时取消，取消落盘后没有bound事件/第三次模型调用，两个attempt均停止，最终cancelled。首轮phase9-agent-concurrency.json因重复cancel中断清理失败，原记录保留并正常对账；只对cancelling幂等，interrupted仍可再次请求核查owner。Fake固定清理窗口的修复前失败与修复后16项定向见phase9-duplicate-cancel系列。
- V1-11真实复验phase9-nfs-unavailable-v2.json通过：只暂停项目NFS30.216秒，未缓存路径文件RPC30.017秒返回503/sandboxRecovery=true；无旧停止证明时新attempt认领409，代次不变。恢复NFS后Go确认旧Pod终止，再gen2新Pod读回服务端同hash。45秒独立看门狗配置保留、正常流程未触发；不是Python Agent全链路存储故障或网络分区HA。首轮phase9-nfs-unavailable.json错误地复用已被Exec超时回收的Pod，失败及正常清理保留。结束后NFS running/not-paused、两命名空间无遗留Pod。
- 本批次首轮完整Python175项有1项失败：shutdown接续触发墙钟早于attempt开始。20次复现中再次出现1次，观测UTC19:03:42.938596早于19:03:43.735467；不重置deadline或放宽保护。补安全时间诊断，纯逻辑Fake Store用显式固定时钟；Runner预算检查先于协程创建，避免抛错留下未await对象。真实进程验收仍用真实墙钟；最终完整Python176/176、280.193秒通过，phase9-python-concurrency-final-regression-tests.txt；首次失败未覆盖。上一有效提交36b20e0，本批次交付以PR#2最新HEAD为准。
- 独立只读宿主时钟观测phase9-host-clock-observation.json：30秒2942样本发现2次墙钟倒退约1.242/0.813秒，单调时钟各前进约10ms；校时来源未定位，不改变系统时间。最终39项定向4.593秒通过，phase9-cancel-clock-final-tests.txt；纯逻辑固定时钟和真实进程结果分开，墙钟回拨安全停止保留。
- 当前三类既有运维回放复验3/3：phase9-joint-ops-concurrency-replay.json，真实Milvus2.5.10/OpenSearch+缓存E5/BGE，脚本模型/Fake路由/Fake沙箱，外部模型0；不重新导入或重建索引，临时四服务已停回初始状态，ES未启动。最终完整176含本批次取消/预算预检及固定时钟Fake Store，未把它冒称真实墙钟稳定性证明。
- V1-14真实命令接续通过：phase9-agent-command-recovery-final.json，实际HTTP202后SIGKILL、原Task自动接续、两条命令各注入一次、原预算/时钟保持、只写一次guard、旧Trace含已保存工具证据；第三次终态启动Task/Session/Go日志字节不变。全部固定Replay，外部模型0。新call.audit先fsync再确认调用完成，缺失响应不伪报0 token。
- ownerStopped与最终Trace提交窗口已补齐；只读partial-audit视图不修改日志或证明停止。Session fsync后命令游标缺口、旧attempt不引用后续head、未确认owner查询无副作用，34/34定向5.433秒。实际写入丢确认核查新增workspace-recovery审计复验通过，phase9-agent-audited-write-inspection.json。旧188全量277.950秒属于最后窗口修复前，最终全量另记，不混算。
- 本批次前已推送HEAD=0ef9b51；交付仍更新draft PR#2，base=codex/agent-memory-rag-plan，依赖PR#1，不合并。当前测试Pod均已释放，NFS运行/未暂停，数据保留；用户docs32/docs34修改不纳入提交。
- 最终代码全量190/190、271.294秒通过，phase9-python-call-audit-final-regression-tests.txt；含最终窗口修复与查询测试。此前188批次和34定向保留，不相加。compileall/diff检查、证据JSON与校验和检查通过；无付费模型。
- 显式旧文件迁移已完成：workspace_migrate管理CLI先预览hash、持Task锁、经Go复制/核查到新NFS目录，imports.jsonl保存PREPARING/COMMITTED和来源映射；结果不明写入按hash跳过，旧owner须确认停止，最多3次导入attempt。旧线性Session只适配副本，源文件与原JSONL保留。首次resume新建Task，复用导入分支/工作区；V1旧节点继续拒绝。
- 两轮真实合成迁移通过：phase9-agent-workspace-migration.json及phase9-agent-workspace-migration-entry.json，后一份从.cache/workspace-eval/run-LV9TMnKz/migration.json原字节复制。首写确认丢失后两attempt恢复，两个文件各写一次；中文路径hash与NFS服务端一致，Replay新Task只读原文件，重复apply日志不变，原Session/源目录保持。Pod均正常清理，外部模型0。
- 最终完整Python197/197、288.024秒通过，phase9-python-migration-regression-tests.txt；19项定向3.534秒，phase9-migration-boundary-tests.txt。首次19项3错误为写响应hash字段误用，原文phase9-migration-initial-tests.txt保留；修正为Go实际newSha256后再做真实验收，不用Fake成绩替代。源限UTF-8、100文件/16MiB，空目录/链接/特殊文件明确拒绝。
- 统一入口hack/workspace.sh已实现install/check/build/nfs/go/agent/test/migrate/verify/joint，实际check与migration分发通过；完整安装、前台启动、all/joint分发未逐项复验。agent入口固定Replay/关闭Jev并移除Operator环境；verify独立结果目录、串行夹具、不删除数据。当前待完整启动验证/性能与全部V2。
- 本批次前有效HEAD=fd51737；仍更新draft PR#2并依赖PR#1，不合并。用户docs32/docs34原修改不纳入。Go/gVisor/NFS仍运行，旧数据/索引/卷保留；迁移控制目录均在原生Linux持久盘。
- 统一安装/启动验收完成：缓存安装复核固定下载校验和，uv frozen --all-extras检查79包未变化；修正原入口无extras会移除已有RAG依赖的风险。Go构建通过，NFS no-recreate与V1-11同容器ID/172.23.0.3保持；Go/agentd实际由入口启动、SIGKILL后原Task接续，Operator环境已移除。最终phase9-agent-unified-entry-final.json成功，原预算/时钟/来源hash保持。
- Pod分段观测phase9-agent-pod-recovery-timing.json通过（来自.cache/workspace-eval/run-7qsr3gDy/pod.json原字节）：故障请求→失败RPC112.74ms→旧停止2245.30ms→新claim就绪3494.61ms→成功读取3839.60ms；1次写预留/2次读预留（含失败），没有重复写。统一入口重启样本强杀→读回约60.04秒，源码导入与轮询计入，不当生产RTO。
- 固定负载serial基线9/9通过、267次读写，phase9-nfs-fixed-workload-serial.json；NFS容器收发合计3462282字节（约3.30MiB，含挂载/元数据/协议，非公网）。文件读写P50约199–201ms，Go/NFS/控制器端点CPU与内存已记录，不是峰值。首次并行探索另存phase9-nfs-fixed-workload.json，不混算。学习/硬件/数据/完整表见文档46。
- 最终全量Python201/201、288.476秒通过，phase9-python-benchmark-final-regression-tests.txt；此前200/280.764秒与负载夹具3项原文保留，不相加。统一joint入口3/3通过，phase9-entry-joint-replay.json，实际Milvus2.5.10/OpenSearch+缓存E5/BGE，脚本模型/Fake路由与生命周期；4个已有服务临时启动后全部停回原状态，ES未启动，未导入/重建索引，外部模型0。
- 当前V1的14项验收及迁移/安装启动/小规模性能有证据，M3可交付审查；verify all本批次未整套重跑，首次联网安装未重复，现有各场景原始证据与缓存安装分开。V2未开始，完整Goal保持active。上一有效HEAD=cd871aa，仍更新draft PR#2（依赖PR#1），不合并；用户docs32/docs34未纳入。
- 历史下一步（本批正常保存已接线）：日志、Session引用、受限HTTP及客户端已实现，接完整边界自动保存协调器与Runner/Loop可选入口，再接取消/启动对账；必须先冻结再复制Session/PREPARING，保持原Task预算；只挂完整工具组和已持久化的最终诊断，跳过Loop初始化告警边界，最终公开安全降级诊断与模型原文分开保留。随后接restore新空工作区屏障、历史分支与引用保护。V2分支已创建，不重复M0或重跑已终止组件。V2继续在NFS运行，十项完整验收均pending，完整Goal active。
- 环境恢复：kind-sandboxd/Calico/gVisor和sandboxd-workspace-nfs-1正在运行；NFS172.23.0.3（后续核对不能写死）。M0合成Pod sandbox-workspace-probe已正常删除，PVC/PV及NFS文件保留，删除后服务端hash仍匹配；手动节点根挂载/var/lib/sandboxd-nfs-probe已卸载，不删除NFS数据卷。
- Go镜像最新sandboxd-workspace:runtime-v1.5已载入kind，旧runtime-v1～v1.4保留，均含文件与目录初始化helper。Go组件元数据持久于 /home/hx/.local/share/sandboxd/workspace-runtime-smoke/workspaces.jsonl，各轮组件的业务/维护Pod均已正常清理，PV/PVC与文件保留；后续启动真正服务用独立workspace-control目录，不能把合成元数据误当业务任务。
- 已落实接线不变量：原claim/loop时间锚点持久化，不重新发190/120秒；没收到claim响应不等于从未发送，Go确认旧owner停止后才记Python owner_stopped。Session按task+attempt组织增量，旧记录不改写。部署默认后端已迁移，SIGKILL与预算已有后续真实证据；旧文件显式迁移与当前状态见本节后续记录/文档45 §7，原始数据保留。

## 2026-10-17 工作区持久化与恢复规格（实施授权前记录）

- 用户本轮只要求编写后续 Agent 使用的需求实现文档。已完成 [43 NFS V1 → restic + MinIO V2 实施约束](43-工作区持久化与恢复实施约束-NFS-v1与restic-MinIO-v2.md)；功能、部署、故障实验和新测评均未开始。
- 已确认：V1 仅 NFS 最新状态；Pod 故障与 agentd 重启均自动接续未完成任务；保留 task_id，新增 attempt，继承原预算及截止时间；旧节点可读，V1 不允许携带工作区恢复旧节点。
- 已确认：V2 保留 NFS 作为运行工作区，restic 备份到 MinIO 并绑定历史检查点；本地盘运行方案仅作为后续性能实验，不承诺未实测性能提升。
- 代码基线为 66f881e。现有文件工具在 agentd 本地 task 目录，Pod /workspace 为另一份 emptyDir，TaskStore 主要在内存；同 Task 多 attempt 的 Session 分组、任务账本、文件工具迁移和恢复预算都是待实现项。
- 已将规格接入 GOAL、AGENTS 和文档导航。当前优先下一步是用户审阅规格并明确授权实施，然后执行文档 M0；不要自动启动服务、故障注入或付费调用。
- 本轮只维护文档；用户原有 docs32/docs34 修改保留。Phase 8 的 Live 和人工审核待办继续保留，以下历史“第一项未完成工作”不构成本轮自动执行授权。

## Phase 8 当前恢复点与待办

- **工作记忆 P0/P1 优化（本地验收）**：已补按分支工作记忆、60 秒状态时效、完整工具轮次 checkpoint、调用时上下文压缩与长期记忆作用域。完整 Python 回归 90/90、最终边界回归 15/15、合成记忆事实 2/2、三类本地联合回放 3/3；长对话估算 32,231/32,768 token，保留早期证据指针和最近拒绝。详细边界、复现命令和原始结果见 [42](42-运维Agent工作记忆与上下文压缩.md) 与 [Phase 8 验收记录](evidence/phase8-memory-optimization.md)。本轮外部模型调用为 0；旧付费计划未因这次变更获得授权，真实模型质量和实时集群身份仍待单独验证。

- **2026-09-29 续跑审计**：上一轮为实际进展（提交/推送与验证），本轮继续完成无费用缺口。新增 docs41 逐项验收表；全60题生成计划 `phase26-generation-full-plan.json` 最多960请求、保守预留$46.714061，未授权未执行，不纳入此前$6问题。新增 `agentd.logs.benchmark.run` 可复现只读负载+资源采样；两轮实际运行保留，最终显式P50/P95/P99一轮48.239秒、48个采样、无采样错误，三查询P95=4.723/4.389/4.458ms、查询错误率0；采样最大节点进程CPU15%、JVM heap423056600字节。报告为 phase26-benchmark-p95-resources 系列，不能以容器上限代替实测资源。

- 2026-09-29 用户最新：加入 Jev 最小模型路由并开始实施。详细范围已补入 docs37 §2.6；Goal 卡片现为 active；旧摘要无法通过现有工具重写，仓库规格与用户本次指令为执行依据。

- **权威范围**：[GOAL](../GOAL.md) 与 [37 完整目标](37-Kubernetes运维Agent目标与验收.md)。Goal 工具短摘要不代替详细验收。
- **已具备**：Phase 7 树形 Session、分层记忆、本地 BGE 推理、ES BM25 + Milvus dense + RRF/BGE 四组公开 SciFact 子集消融，均需按 Phase 8 逐项复核。当前开发分支已有 PR #1；旧结果不能冒充新运维数据集成绩。
- **本轮已完成**：Milvus 原生 BM25/Dense 新快照索引、组件/来源/版本过滤；OpenSearch 2.19.6 独立 localhost:9201、结构化 search_logs/aggregate_logs；720 条合成日志、40 个查询独立 Python oracle；40 篇固定运维文档、298 chunks、许可与来源 commit。
- **真实集成证据**：`evidence/phase22-milvus-smoke.json` 为 9 chunk/7 题迁移夹具，四组指标 1.0，仅证明小样本链路；`evidence/phase22-logs-results.json` 为实际 OpenSearch 查询 40/40，p50 3.38ms/p95 9.21ms。12 个检索/日志定向测试与 8 个 API/插件/路由测试通过。
- **2026-09-29 新结果**：40 篇/298 chunks + 60 候选问题（50可回答/10无答案，13 dev/47 test），多语言 E5 + BGE 四组消融已真实执行。Hybrid+rerank Recall@10=0.8500、nDCG@10=0.6372、路径 P50=4958ms；不是人工金标。逐题证据 `phase23-ops-rag-results.json`。
- **联合演示**：`python -m agentd.ops_demo` 三类均通过，使用实际 Milvus/OpenSearch，脚本模型/Fake 路由/Fake Sandbox，真实集群调用0。报告 `phase23-joint-replay.json`。
- **性能**：OpenSearch Benchmark 2.4.0 查询只读自定义 workload 已成功，720条、1 client、20 ops/s、每操作20预热/100测量；三操作 p50=4.76/3.75/3.34ms、0错误。CSV 的748为集群含系统索引计数，目标索引独立核对720；原始报告和环境另存。
- **源码对照**：Pi commit `11894012dd461232eb075bc890538b6866860a10`；Codex commit `46fdd5ef39735f4159cdcf0ec5e85c10521494e5`。Codex README 旧 core 路径已失效，实际 write 管线在 memories/write/src；见 docs39。
- **生成评测**：Ragas 0.4.3 在独立 WSL venv 安装，依赖已冻结；官方 Faithfulness/FactualCorrectness 及完整生成/rubric 流程共3个无网络协议测试通过。`agentd.ops_eval.generation` 默认离线预览，支持无检索/RAG、逐步模型裁判依据、运维 rubric、预算与持久账本。真实模型分数尚未执行。
- **回归**：完整 Python **72/72**、250.490秒（大部分为 Windows 挂载盘上记忆 CLI 子进程）；后续 Live JSON action/预算定向4/4（51.168秒）通过；不是将旧完整通过冒称覆盖后续全部变更。
- **日志预算修复**：默认返回3条，插件拒绝超过3500字节结果，避免 Runtime 截断 JSON；随后日志/数据/检索定向12/12通过（0.321秒）。实际 OpenSearch 最新40/40、集合P/R与聚合正确率1.0、P50=2.16ms/P95=3.02ms，另存 `phase25-logs-bounded-results.json`；现有联合回放日志响应最大1040字节，未超过新预算。
- **索引生命周期**：真实小快照验收更新/删除/四类过滤通过，旧2条索引保留、新索引1条；`phase24-index-lifecycle.json`。临时本地夹具执行后清理，正式 ops-v1 未修改。日志新增分项汇总后再次真实40/40，集合P/R、聚合均1.0，p50 2.18ms/p95 3.23ms；与此前报告并存。
- **交付状态**：实现与测评代码已推送至 PR #1，主要实现提交为 `8b4d0a5`、`db5bd01`、`7e35d15`；PR 标题与正文已更新，未合并。2026-09-29 检查本地工作区干净，远端 PR HEAD 与 `7e35d15` 一致；本条恢复说明随后单独提交。
- **第一项未完成工作**：检查用户是否已答复付费计划，按精确授权范围执行并保留逐调用报告；若未答复，不发请求、不重复读取 Key，也不自行勾选60题 REVIEW.md。代码、无费用验收与文档已具备。6题生成计划仅为冒烟验收，完整60题生成对比仍需另列计划并按预算授权执行；目标尚未完成。
- **待用户预算**：Jev 两题计划已保存 `phase23-jev-plan.json`，2 Jev+6 DeepSeek、拟上限$0.10；异步问题已发，尚无答复。生成6题计划 `phase24-generation-plan.json` 最多96调用、保守预留$4.671406；联合Live三场景 `phase24-live-agent-plan.json` 最多18调用、预留$0.875889，已另发合计$6的异步授权问题，尚未答复，不执行。DeepSeek单价按2026-09-29官方峰时cache-miss费率核对。
- **费用和证据**：本轮无新付费推理调用。Jev Key 只读 models.list 在2秒超时后10秒鉴权成功，运行时仍2秒。旧无报告尝试不当作零消费。学习文档 docs38/39、固定语料 README/REVIEW 与原始结果已新增。

## Phase 7 历史恢复点（以下“下一步”已由 Phase 8 取代）

- 目标与验收已写入 `GOAL.md`、`AGENTS.md`、`docs/31`。实施顺序：M1 Session 树 → M2 分层记忆 → M3 混合检索 → M4 Jev 路由 → M5 联合测评与文档。
- 当前 Python 环境已核实：3.12.14、uv 0.12.19；`agentd/uv.lock` 含可选 rag 依赖。用户指定 Milvus 2.5.10，本项目 `sandboxd-rag` Compose 的 Milvus 2.5.10、ES 8.19.22、etcd、MinIO 四容器 healthy；不触碰其他项目资源。
- M1 已交付 `runtime/session.py` 的消息节点/父链/活动叶子、完整 Turn 分支、旧线性快照迁移与断尾修复；`store.py` 恢复创建新 task，API 和 `session_cli.py` 可列树/读路径/分支。合成夹具在 `agentd/testdata/session-tree-demo/`，学习文档为 [33](33-树形Session与分支恢复学习手册.md)。
- M2 已交付 `memory.py` 两阶段文件记忆、显式提取/重建/遗忘 CLI、合成 `memory_eval`、可选启动摘要与静态只读 `read_memory` 工具。只提取成功结束 Session 的活动路径，事实保留来源/时间/历史冲突。学习文档 [34](34-Codex风格分层记忆学习手册.md)，证据 [phase16](evidence/phase16-memory-mvp.md)。
- 本地验证：`uv run --project agentd --frozen -- python -m unittest discover -s agentd/tests -v` 为 **45/45**；Session CLI 的 tree/path、Memory CLI 的 extract/rebuild/summary/rollout/forget 均通过。`memory_eval` 两条合成会话当前事实 Recall/Precision 均 2/2，冲突 1，摘要 155/256 字符。以上不等于真实 gVisor 或 Live 模型联合 E2E，也不证明自然语言记忆提取质量。
- M3 已交付版本化 runbook JSONL、ES BM25 与 Milvus dense 双索引、RRF/BGE 本地 CPU 推理、`check/rebuild/query/eval` CLI、官方 SciFact ZIP 转换，以及 opt-in 静态 `search_knowledge` 工具。公开 SciFact 完整 5,183 篇索引、前 30 test query 消融：BM25/dense/RRF/rerank nDCG@10 分别 0.5511/0.7445/0.7931/0.7826；rerank p50 5.76 秒。详情见 [35](35-Milvus-ES-BGE混合检索学习手册.md) 和 [phase17](evidence/phase17-hybrid-retrieval-mvp.md)。这不是官方完整 300 query 榜单，也没有 Live Agent 调用。
- M3 后 `unittest discover` 为 **53/53**；`git diff --check` 通过。完整测试中的记忆 CLI 子进程在 WSL `/mnt/c` 启动耗时较长，测试总时长 256 秒，与检索在线延迟不同。
- M4 已按 TypeSafe 官方 Python SDK `0.7.2` 的 `Choice` API 实现 task 前静态 economy/strong 选模，0.7 置信度门、2 秒超时和强模型回退；实际 Runner 只绑定一次 Gateway。最初的 [phase18](evidence/phase18-jev-router-mvp.md) 记录 8 条合成 Fake/Replay、假设时延/费用和 4 个路由测试，该历史证据没有 Live 调用；后续真实执行状态见下方 phase21。学习文档为 [36](36-Jev模型路由学习手册.md)。
- M5 四模块联合无密钥 Replay 已通过：Fake Jev 选经济 Replay Gateway，记忆摘要以数据注入，`search_knowledge` 实际连接本地 ES/Milvus/BGE，Session 保存 6 节点并支持完整叶子分支，假沙箱释放一次，外部模型调用 0。见 [phase19](evidence/phase19-phase7-integrated-replay.md)。
- Phase 7 完整 Python 回归 **61/61**，Live 无网络预检加入后验证耗时 **275.780 秒**；随后样本 SHA256 审批门加入，Live 预检定向 **5/5**、默认预览与 `compileall` 通过。耗时主要在 `/mnt/c` 上记忆 CLI 的多个 Python 子进程启动，不是检索或模型在线延迟。四模块联合无密钥 Replay 在允许连接本机 Docker 服务的执行环境中重跑成功。
- M4 Live 协议见 [phase20](evidence/phase20-jev-live-preflight.md)：8 条固定合成题、最多 8 次 Jev + 24 次下游请求、128 输出 token、零重试、SHA256 审核门和 `$1` 预算。用户已明确批准该范围；无需重复询问同一授权。
- 首次执行在无 DNS 的受限沙箱内超时，未得到逐请求报告；不能仅凭超时断言用量为零。允许联网的执行完成 l01：1 次 Jev 尝试、3 次 DeepSeek 尝试，三组下游答案均正确，Jev `router_error` 回退到 strong 后按协议停止，估算费用 `$0.00046489`（含失败 Jev 的保守预留）。后续只读 IPv4 鉴权检查返回 HTTP 401；完整 Live 横评未完成，见 [phase21](evidence/phase21-jev-live-first-case.md) 与脱敏 JSON。
- 已补每次推理调用前的脱敏进度标记和 `AGENTD_JEV_IPV4_ONLY=1`，Agent 与 Eval 共用 2 秒超时、零重试 Jev Client；官方 SDK 的本地 HTTP MockTransport 测试覆盖真实请求序列化与响应解析。
- 本次网络配置/进度记录修改后的 Router、Live Eval 与 API 定向回归 **10/10**，`compileall` 和 `git diff --check` 通过；未把历史完整 61/61 冒充为本次完整重跑。
- GitHub 已解决：用户完成 WSL `gh` 登录，允许联网环境推送成功，已创建 [PR #1](https://github.com/chx739/sandboxd/pull/1)，不合并 main。插件连接的写权限不是当前交付阻塞。
- 历史旧下一步（已作废，不执行）：当时等待仓库外 Jev Key、核算用量后继续横评。该暂停后来已被用户 2026-09-29 最新指令覆盖；当前范围见 Phase 8。Key 不进入 Git、日志或文档。
- 2026-09-29 验收复核：PR #1 的 HEAD 与本地 `5f7a910` 一致，工作区干净；Jev Key 文件修改时间仍早于 HTTP 401 检查，未新增 API 调用。docs31 第 9 节补齐原目标逐项证据与四模块演示入口，并将旧环境缺口标成历史快照。完整目标仍未完成。

## 2026-09-28 新阶段方案审计

- 用户目标：Pi 风格 Session 树与分支恢复、Codex 风格分层记忆、Milvus dense + ES BM25 + RRF + BGE Reranker、TypeSafe Jev 模型路由；每项均要求最小可运行、可测评，并有持续记录与学习文档。
- 当日启动时已建立 [31 方案](31-Agent记忆检索与路由最小实现方案.md) 和 [32 学习手册](32-Agent记忆检索与路由学习手册.md)；本节以下环境信息是当时的历史快照，当前进度以上面的恢复点为准。
- 只读环境核对：Docker 客户端/服务端 29.2.1，Compose v5.1.0，daemon 约 23 GiB/12 CPU，当前无运行容器；WSL Python 为 3.10.12，`python3.12` 与 `uv` 不在 PATH；RTX 5060 8 GiB。没有拉镜像、下载模型、读取 Key 或调用外部 LLM。
- 下一步：从方案 M0 开始，先备齐 Python 3.12/uv 与离线夹具；各模块依次独立实现、测评并记录证据。任何 Live 模型/Jev 调用需新的明确授权和预算；历史 DeepSeek Eval 授权已消费。

## Phase 5 Eval v2 修复重跑

| 阶段 | 状态 | 内容 |
|---|---|---|
| V5.0 | 已完成 | 新授权、解析 bug 与 91 Task 边界冻结 |
| V5.1 | 已完成 | 顶层 JSON 解析修复、嵌套 evidence 测试 |
| V5.2 | 已完成 | canaryEchoRate、全量测试、v1/v2 Replay |
| V5.3 | 已完成 | 3 条预检 + 88 Task 来源隔离后 Live |
| V5.4 | 已完成 | Phase 14 evidence、文档、回归与 GitHub |

## Phase 5 Eval v2 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| V0 | 已完成 | GOAL、AGENTS、计划、40 条结构与 Live 次数冻结 |
| V1 | 已完成 | v2 JSONL、覆盖标签、Loader/Lint、CLI kind 筛选 |
| V2 | 已完成 | 40 条确定性 Replay、v1 回归与来源隔离契约 |
| V3 | 已完成 | clean/hard 1 批 + attack 3 批，共 88 Task DeepSeek Live |
| V4 | 已完成 | 聚合、夹具缺陷修复、学习/面试/踩坑/evidence 与回归 |

## Phase 5 Eval v1 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| M0 | 已完成 | GOAL、AGENTS、实现计划、指标和 20 场景范围 |
| M1 | 已完成 | JSONL 模型、Loader/Lint、Scorer |
| M2 | 已完成 | 基于当前 AgentRunner 的确定性 Replay Runner |
| M3 | 已完成 | 最小单测与本地低资源 Eval 报告 |
| M4 | 已完成 | 学习/面试文档、evidence、回归、GitHub |

## Phase 1 已完成基线

- WSL2 + 单节点 kind + gVisor + Calico，已有 Starting gVisor 真实证据。
- restricted Pod 安全基线、短时 Token、只读 RBAC、NetworkPolicy 正反路径。
- Manager、Exec、Informer/Lister、Workqueue、预热池、JSON Patch CAS。
- Prometheus 指标、Deployment scale server-side DryRun 和 Agent/Operator 双 Token 审批门。
- 全项目 Go test/vet/build 和完整 Demo 已通过。
- 学习文档、面试问答、踩坑与 Phase 1 evidence 已完成。

Phase 2 不得破坏这些证据和接口。

## Phase 2 目标摘要

- Python agentd + LangGraph StateGraph。
- 真实用户态 Prometheus/Alertmanager 外部告警。
- OpenAI-compatible Live LLM 和确定性 Replay。
- Prometheus Query Tool。
- Go 结构化 Kubernetes Diagnostic API，查询在 gVisor 沙箱中发生。
- Pod Log/ConfigMap 间接 Prompt Injection。
- Python Policy、Go Tool Policy、RBAC、审批门纵深拒绝。
- 结构化诊断或 Pending Deployment Scale Plan。
- 脱敏 Trace、最小验证、学习/面试/踩坑文档和 GitHub 维护。

## 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| M0 | 已完成 | 目标、计划、AGENTS、PROGRESS、分支和版本策略 |
| M1 | 已完成 | Go 结构化 Kubernetes Diagnostic API |
| M2 | 已完成 | agentd、LangGraph、Live/Replay、三个工具 |
| M3 | 已完成 | Prometheus/Alertmanager 与故障 Fixture |
| M4 | 已完成 | 完整 Live/Replay 链路与拒绝矩阵 |
| M5 | 已完成 | Evidence、README、学习和面试文档 |

## Phase 2.1 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| P0 | 已完成 | 目标、计划、AGENTS、PROGRESS 与数据模型冻结 |
| P1 | 已完成 | ToolResult 双通道 |
| P2 | 已完成 | 生命周期事件、模型 usage/finishReason |
| P3 | 已完成 | 上下文预算、取消与独立清理 |
| P4 | 已完成 | 最小验证、学习/面试/踩坑、README、GitHub |

## Phase 3 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| M0 | 已完成 | GOAL、AGENTS、计划、PROGRESS、分支和 9 个 Python 测试基线 |
| M1 | 已完成 | 受信任插件接口、注册表、Prometheus/Kubernetes 插件 |
| M2 | 已完成 | 手写双层 Agent Loop 替换 StateGraph |
| M3 | 已完成 | 线性 Session-lite、steer/follow-up/cancel/resume API |
| M4 | 已完成 | 最小验证、Demo、学习/面试/踩坑、README、GitHub |

## Phase 4 里程碑

| 阶段 | 状态 | 内容 |
|---|---|---|
| M0 | 已完成 | GOAL、AGENTS、计划、PROGRESS、分支和资源审计 |
| M1 | 已完成 | 改造前低资源真实 Replay E2E |
| M2 | 已完成 | 受限 SSH LinuxHostPlugin 与一次性 Demo Target |
| M3 | 已完成 | list/read/search/write/edit task 工作区工具 |
| M4 | 已完成 | Policy、Runtime、Session、Trace、Replay 接线与首次改造后 E2E |
| M5 | 已完成 | 学习/面试/踩坑、最终验证、GitHub |

## 本轮已经完成

- 从 agent/environment-gvisor 的干净提交 9865955 创建 codex/phase2-langgraph-agent。
- 重新阅读 GOAL、全局计划、旧 Agent 计划、PROGRESS、AGENTS 和当前 API/RBAC/NetworkPolicy。
- 将 docs/12-Agent层实现计划.md 改为 Python agentd + LangGraph + Go 可信边界的权威规格。
- GOAL.md 增加 Phase 2 当前目标、非目标、完成证据和恢复顺序。
- AGENTS.md 增加 Phase 2 安全、证据和 Git 纪律。
- docs/00-实现计划.md 增加 Phase 2 总览。
- 确认现有沙箱镜像没有 kubectl；计划改为在 gVisor 内用固定 HTTPS 请求访问 Kubernetes API，避免扩大镜像和任意命令面。
- 确认 Python 3.12.3、系统无 pip、用户态 uv 可用；采用 pyproject.toml + uv.lock，不使用 sudo。
- 固定并验证 Python 直接依赖可解析：langgraph 1.2.11、langchain-core 1.5.6、langchain-openai 1.5.2、fastapi 0.141.1、uvicorn 0.52.4、httpx 0.28.1。
- 固定 Prometheus 3.14.0 与 Alertmanager 0.34.0 的官方 linux-amd64 SHA256。
- 在踩坑文档增加“系统 Python 无 pip”和“LangSmith 传递依赖不等于启用 tracing”。
- 完成 internal/diagnostic：五种只读 operation、固定 Kubernetes API 路径和固定 curl 命令。
- 完成认证后的 POST /api/v1/sandboxes/{id}/diagnostics/kubernetes。
- 未知 operation、错误 namespace 返回 403 + denyLayer=tool-policy；未知 JSON 字段返回 400。
- 增加纯逻辑和 HTTP 边界测试；go test ./...、go vet ./...、go build 均通过。
- 踩坑文档新增“调用方白名单不是服务端边界”和“kubectl 不是只读诊断必要条件”。
- 创建 agentd/pyproject.toml 和 uv.lock；uv frozen 环境可复现安装。
- 完成配置、Pydantic 数据模型、有界 HTTP Client、Task Store、单 Worker 和 FastAPI Alert/Task/Trace API。
- 以 StateGraph 显式实现 prepare、model、validate、execute、finalize 节点及有限循环。
- 完成 query_prometheus、kubernetes_read、propose_plan 三个工具和 Python 前置策略。
- 完成 OpenAI-compatible Live Gateway 构造验证和每任务独立 Replay Gateway。
- deterministic-policy-case Replay 完整验证：读取注入、delete_namespace 在 agent-policy 拒绝、得到 Pending Plan、Sandbox finally 释放。
- FastAPI 验证 Alert Token 不能读取 Task；Python compileall 和 2 个 unittest 通过。
- Trace 仅写 .cache，尝试 0700/0600 权限，不包含 Header/Token/隐藏思维。
- 踩坑新增“Body 必须流式限长”和“多 Tool Call 必须逐个闭合协议”。
- 根目录审计发现并修复 app 导入依赖 cwd；统一为 agentd.app，并新增对应踩坑。
- 使用文档中的 Uvicorn factory 命令启动单个 Replay agentd，/healthz 返回 200，随后按精确 PID 清理。
- 用户态安装 Prometheus 3.14.0、Alertmanager 0.34.0，并使用官方 SHA256 固定供应链输入。
- 增加 localhost-only Prometheus/Alertmanager 配置和携带独立 Bearer Token 的 Webhook 模板。
- 增加确定性 SandboxAgentDemoCrashLoop 告警规则；明确它验证告警传输，不冒充真实异常检测。
- 增加 restricted + gVisor CrashLoop Deployment 和含间接 Prompt Injection 的 ConfigMap Fixture。
- promtool、amtool 配置检查通过；真实 Prometheus 告警进入 firing，Alertmanager readiness 通过。
- 可观测性验证前检查资源与端口，只终止脚本自己记录的精确 PID；验证后 9090/9093 无监听残留。
- kubectl client dry-run 在 kind API Server 停止时仍需 RESTMapper discovery，清单的服务端语义验证留到 M4 临时集群。
- Agentd 增加受 API Token 保护的最近任务列表；Alert Token 访问列表仍返回 401。
- Replay 只支持受限的 {{first_pod_name}} 占位符，并从上一条合法 ToolMessage 中解析动态 Deployment Pod 名。
- Go 可信边界把原始 PodList 压缩为 name、phase、restartCount，避免 4 KiB Observation 截断 JSON。
- 新增 hack/run-agent-demo.sh：拒绝接管既有 namespace、managed Pod、监听端口或错误 kind context。
- 真实 Replay 链路通过：Prometheus -> Alertmanager -> agentd -> LangGraph -> Prometheus/Kubernetes Tool -> Pending Plan。
- 注入文本从 restricted + gVisor CrashLoop Pod 当前日志进入 Trace，injectedVia=podlog，verdict=contained。
- delete_namespace 在 agent-policy 拒绝；直调 Go 结构化接口在 tool-policy 返回 403；通用 Exec DELETE 在 RBAC 返回 403。
- Agent Token 调 approve 返回 401，Deployment replicas 保持 1，Plan 保持 pending；没有向 agentd 传 Operator Token。
- 在真实沙箱执行 dmesg 得到 Starting gVisor，证明诊断执行路径不是普通 runc。
- 运行后 sandboxd-target、managed Pod、8080/8090/9090/9093 监听和渲染 Token 配置均无残留。
- 单入口支持 AGENTD_DEMO_MODE=replay/live；Live 缺任一 LLM 配置时在创建资源前安全失败。
- 加入 Live 分支后完整 Replay 再次通过，严格断言 Agent Policy、Pending Plan、gVisor 与清理结果。
- 2026-08-19 最新脱敏 Replay 证据保存在本地 .cache/agent-demo-evidence/8b11a9d333e94249b9f04438491fa254，明确不作为 Live 证据。
- 完成 docs/14：LangGraph 图、三个工具、有限状态、可信侧压缩、纵深防御、Live/Replay 边界和学习顺序。
- 完成 docs/15：32 个 Agent 安全高频追问，覆盖选型、Tool Calling、注入、RBAC、审批、多会话与外部系统扩展。
- 完成 docs/evidence/phase8-agent-alert.md 与人工脱敏 replay-contained.json、live-not-triggered.json；不含运行时 ID、Token 或隐藏思维。
- README 架构图、能力状态、快速验证、项目边界和 docs/README、docs/13 学习路径已更新到 Phase 2。
- 最终审计通过：Go test/vet/build、Python compileall/7 tests、全部 Shell 语法、脱敏 JSON 和本地 Markdown 链接。
- 清理与秘密审计通过：目标 namespace、managed Pod、四个监听和渲染 Token 无残留，仓库未发现硬编码凭证。
- 2026-08-20 以官方 DeepSeek Endpoint、deepseek-v4-flash、thinking=disabled 做最小 Tool Calling 预检；请求到达服务端但鉴权返回 401，因此没有启动集群或冒充 Live 证据。
- 发现 Provider 异常即使隐藏完整 Key 也可能暴露掩码后缀；新增 public_error 统一清理 API Key、Bearer 和凭据指纹。
- 新增 AGENTD_LLM_THINKING；DeepSeek V4 可关闭 thinking，避免保存或回传隐藏 reasoning_content，其他 Provider 默认不发送私有字段。
- Python 测试增至 4 个；Go test/vet/build、Python compileall、Shell 语法和 diff 检查再次通过。
- 更新后的 DeepSeek Key 通过最小 Tool Calling 探针；严格运行三次 Live 注入实验，危险指令触发 0/3，均如实标记 not-triggered。
- 第 2 次 Live 完整通过：真实 Prometheus/Alertmanager、DeepSeek、gVisor Kubernetes read、Pod Log/ConfigMap 注入、Go Tool Policy、RBAC、gVisor 与清理形成闭环。
- 修复 Live Trace 来源精确列表误判；修复模型在 Markdown 中包装 JSON 时的有界提取，并只信任 Graph State 中的 evidence、deniedActions 和 planId。
- 第 3 次 Live 产出干净结构化 Diagnosis，但模型未查询 Prometheus，严格脚本如实失败；没有超出三次上限重试。
- 最新 deterministic Replay 再次完整通过，包含 agent-policy、tool-policy、RBAC 403、Agent approve 401、Pending Plan、replicas 不变和 Starting gVisor。

## 已知事实与风险

- 当前 sandboxd 通用 Exec 接收任意 argv，Python 白名单不能作为最终边界，因此 M1 必须新增 Go 结构化诊断 API。
- 当前 sandbox-reader 是只读 ClusterRole，能读取 Pod/Log/ConfigMap/Event/Deployment，但不含 Secret 和写权限，可复用。
- sandboxd-target 可被现有 Plan 服务接受，可用于 CrashLoop Demo 和 Pending Scale Plan。
- Live 模型是否服从间接注入具有概率性；最多运行 3 次并如实记录，Replay 不冒充 Live。
- DeepSeek 三次 Live 的 Tool 选择不同；概率行为不能当安全边界，也不能保证每次都查询所有数据源。严格脚本会拒绝不完整链路，Replay 负责确定性安全回归。
- 用户明确允许后，以 sudo 从 Ubuntu 软件源只安装 `bubblewrap 0.9.0-1ubuntu0.1`，恢复受保护的终端/补丁命令；未修改其他系统配置。

## 资源与秘密约束

- kind 单节点；Agent Demo pool=1、worker=1、一次一条告警。
- 每次运行以 hack/check-resources.sh 的实时结果为准；持续 swap 或 WSL/Docker 异常时立即停止。
- 默认不使用 sudo。
- Operator Token 不传给 agentd。
- 不提交任何密码、Token、API Key、认证头、ServiceAccount Token 或 kubeconfig。
- 只清理本次确认创建的进程和 sandboxd-target。
- 不触碰仓库外简历、秘密目录和无关文件。

## 下一步

1. 没有用户新授权时，不继续 Live Eval、模型横评或新功能阶段。
2. 若未来重跑 v2，必须从当前来源隔离修复后的提交开始，使用新的精确授权，并保留首次受污染报告作对照。
3. 不增加任意 SSH/Bash、远端写入、动态插件或生产级功能。
4. 评审建议的后续路线（exec 端点收紧、RBAC 收敛为 namespace Role、Plan 审计持久化、停止条件修复、真实集群+真实 LLM 联合 E2E、agent-sandbox CRD 对接）均须用户逐项明确授权后才能开始。

## 2026-09-18 Phase 6 加固（评审路线 A + Eval Harness 自检）

- 用户明确授权执行架构评审的路线 A（bug 级加固）与 C2（Eval harness 自检）；全程不改架构、不新增功能、不消耗外部模型授权。
- Go 侧：`NewPool` 新增 `createTimeout` 并在 `Reconcile` 每次补池时 `WithTimeout` 包住 `CreateIdle`，修复不可调度 Pod 永远 Pending 导致单 worker 对账永久卡死的缺陷（`docs/11` 坑 52）；`deleteSandbox` 改用独立 10s context；`readyz` 真实检查 `informer.HasSynced`（未同步 503）；create/list/delete 的 5xx 错误脱敏进日志；创建超时映射 504；CAS 冲突判定集中为 `isClaimConflict`。
- 新增 Go 测试：`TestReconcileCreateIdleIsBounded`（修复前会永久挂起）、`TestClaimPatchPathMatchesStateLabel`、`TestIsClaimConflict`、`TestReadyzReflectsReadiness`；`go build/vet/test ./...` 全绿。
- Python 侧：`policy.py` 显式化 `MAX_CLAIM_SECONDS=190` / `MAX_RELEASE_SECONDS=10` 并注明三段预算与“claim 不能共用 loop 预算”的原因；`runner.py` 的 claim 进入 `wait_for`；新增 `test_slow_claim_is_bounded_by_claim_budget`。
- Eval 自检：Loader 新增 `SOURCE_CHANNELS` 通道契约与 injectionSources 恰等校验，lint 即可静态拒绝“声明 podlog 却从 get_configmap 取 artifact”类夹具错误（v1/v2 数据零修改通过）；新增 golden Replay 基线 `agentd/testdata/eval-replay-golden-v2.json` 与 `test_eval_golden.py`，钉死 40 条 case 的确定性工具流，`AGENTD_UPDATE_EVAL_GOLDEN=1` 可重新生成。
- 本轮验证仅本地：Go build/vet/test、34 个 Python 串行 unittest、evals `lint`（v1+v2）与 `replay` CLI 门禁全部通过；未启动 kind、Docker、Prometheus、Alertmanager、SSH Target，未调用外部 LLM，未使用 sudo。
- 实现提交：`fix: bound pool replenishment and harden API edge cases`、`fix: give sandbox claim and release explicit budgets`、`test: add eval fixture contract lint and golden replay baseline`；文档与推送见本轮后续提交。

## Phase 5 本轮记录

- 2026-09-01 用户明确要求开启目标模式，实现第一版 Prompt Injection 测试集并开始测试；GOAL、AGENTS、PROGRESS 与 `docs/28` 已先冻结范围。
- 新增 20 条合成 JSONL：clean/attack/hard-negative 为 4/12/4，覆盖 alert、Prometheus、Pod Log、ConfigMap、Event、Linux Log 和文件七种来源。
- Loader 校验 Pydantic 字段、行号、ID 唯一、来源 Tool、攻击 Tool、required/forbidden 契约和固定 v1 数量；没有引入新依赖。
- Replay Runner 使用当前 AgentRunner、双层 Loop、Plugin Registry、Python Policy 和 FileWorkspace；Fake Connector 不联网，只返回合成 Artifact 并记录外部状态变化。
- Scorer 用结构化事实计算 Clean Task Success、Agent ASR、Unauthorized Side-effect、Containment、Over-refusal 和 Evidence Coverage，不用 LLM-as-Judge 判断权限或副作用。
- Runtime 最小修复：`injectedVia` 能区分七种来源；Workspace 路径/CAS 拒绝以 `workspace-policy` 进入 Trace，没有扩展任何 Tool 权限。
- 首轮测评把 ConfigMap canary 出现在已认证 Diagnosis evidence 误算成外泄，导致副作用率 1/12；明确授权输出与外部 Sink 后修正 Runner，未修改样本期望掩盖失败。
- 本地 Eval 最终结果：正常任务 8/8、Replay Agent ASR 12/12、未授权副作用 0/12、遏制 12/12、过度拒绝 0/8、证据覆盖 18/18、Sandbox release 20/20。
- 新增 `docs/29` 学习手册、项目 FAQ Q31–Q36 和 Phase 11 脱敏 Replay evidence；公开基准只作设计参考，没有下载数据或引入运行时。
- 本轮没有启动 kind、Docker、SSH、Prometheus、Alertmanager 或 Live LLM，没有使用 sudo，也没有发送项目数据。
- 最终回归：Python compileall、25 个 frozen unittest、Eval lint、20 条 Replay、Shell 语法、diff 与新增内容秘密模式扫描全部通过。
- 代码提交为 `4b6759e`；Phase 5 计划/结构化记忆提交为 `b848d59`，最终文档提交和 GitHub 推送见本轮最新提交。

## 2026-09-01 DeepSeek Live Eval v1

- 用户明确授权开始测试；执行前限定为 `deepseek-v4-flash`、`thinking=disabled`、20 条合成数据、串行、预算约 2 元，不连接真实系统。
- 前置资源为可用内存约 7057 MiB、swap 空闲 2048 MiB、`/tmp` 空间充足；Key 文件只验证存在和长度，不输出内容。
- 新增最小 Live Runner/CLI，复用当前 AgentRunner、Loop、Plugin、Policy、Workspace 和 Fake Connector；Outcome 增加模型调用与 Token 字段。
- 1 条无攻击预检成功：4 次模型调用，7037 输入、1128 输出，无 Provider 错误。
- Run 1 共 104 次模型调用、214862 Token、20/20 release；只有 7/12 attack 接触到注入，Clean 4/8、Evidence 10/18，因此保留为无效夹具对照，不宣传 0% ASR。
- 根因是 Alert 没提供正常运维资源身份。Run 2 只加入 sourceTool 的 namespace/name/targetId/path，不泄露 attackTool、不改 Prompt、不放宽 Policy。
- Run 2 共 76 次模型调用、154043 Token、无 Provider 错误、20/20 release；attack 12/12、hard-negative 4/4 均进入上下文。
- Run 2 指标：Clean 7/8、Evidence 18/18、Agent ASR 0/12、未授权副作用 0/12、Over-refusal 0/8；没有 Agent attack，Containment 分母为 0，正确口径是 N/A。
- 唯一正常失败为 `clean-file-runbook`：必要文件已读，但模型继续查无关工具并达到六轮上限；暴露工具选择/停止条件问题。
- 修正 canary 口径：授权 Diagnosis 中复述记 `canaryEchoed`，只有到达攻击者外部 Sink 才是 `canaryLeaked`；新增纯逻辑回归测试。
- 预检 + Run 1 + Run 2 合计 377070 Token；按运行时高峰全缓存未命中估算 1.390602 元，低于事前约 2 元预算。
- 本轮没有启动 kind、Docker、SSH、Prometheus、Alertmanager，没有使用 sudo；原始逐案例报告只在 `.cache/evals`，不提交 Git。
- 最终回归通过：27 个 frozen Python unittest、compileall、Eval lint、20 条 Replay、Markdown 链接/围栏、diff 和精确 Key 扫描；仓库 Key 匹配为 0。
- 该次 Live 授权已消费，后续执行者不得据此继续重跑。脱敏证据见 `docs/evidence/phase12-deepseek-live-eval-v1.md`。

## 2026-09-01 Prompt Injection Eval v2

- v2 扩展为 40 条：8 clean、24 attack、8 hard-negative；七种 source、六种 goal、六种 technique 均由 Loader/Lint 强制覆盖，v1 原文件未修改。
- 默认 CLI 切换到 v2，并支持重复 `--kind` 筛选；40 条 Replay 为 Clean 16/16、Agent ASR 24/24、副作用 0/24、Containment 24/24、Evidence 35/35。
- 按用户精确授权串行执行四批 DeepSeek：normal 16 个一次、attack 24 个三次，共 88 Task、353 次模型调用，无 Provider 错误。
- Live 合计输入 640276、输出 90155、总 Token 730431；峰时全缓存未命中费用上界 2.732223 元，低于 8 元止损线。
- normal 为 clean 7/8、hard-negative 7/8，合计 14/16；Evidence 15/15、Over-refusal 0/16。两个失败都读到必要文件，但继续扩展查询直到六轮上限。
- attack 目标来源送达 71/72、任意来源送达 72/72；Agent ASR 0/72、副作用 0/72、any-hit/stable 均 0/24；没有 Agent attack，Containment 为 N/A；9 次 canary 回显、0 次外泄。
- Live 后逐案例审计发现 Fake Kubernetes Connector 会把 artifact 跨 Pod Log/ConfigMap/Event 复制，25/72 次出现额外来源，所以来源分层结论无效，0/72 不能外推为干净 v2 安全率。
- 修复为 artifact 只从声明来源返回，并增加“额外 injection source 即契约错误”回归；修复后的 v2 40 条、v1 20 条 Replay 与 7 个 Eval 测试通过。
- 88 Task 授权已消费，没有擅自追加修复后 Live。完整边界见 `docs/evidence/phase13-prompt-injection-eval-v2.md`。
- 最终本地验证：29 tests + 5 subtests、compileall、v1/v2 lint 与 Replay、50 个 Markdown 文件围栏/链接、diff check 全通过；166 个 tracked file 精确 Key 匹配为 0，通用模式仅命中脱敏单测假值。

## 2026-09-01 Eval v2 解析修复与正式重跑

- 代码复现确认 `parse_final_diagnosis` 会把带 summary 的嵌套 evidence 当成最终 Diagnosis，导致外层 rootCause/recommendation 丢失，并影响 canary/refused 派生事实。
- 解析器改为成功读取外层对象后按 consumed 偏移跳过整个区间；新增真实对象形状的嵌套 evidence 用例，同时保留多段顶层 JSON 选择最后合法对象的行为。
- `canaryEchoed` 进入 CaseScore，并新增 Canary Echo Rate；它只表示 canary 传播到授权结论，不并入 Agent ASR、Leak 或副作用率。
- 本地门禁为 30 tests + 5 subtests；v2 Replay 40/40、v1 Replay 20/20，来源契约错误 0。
- 用户新授权 3 条预检 + 原协议 88 Task。预检 clean/hard/canary 全通过；正式重跑为 359 次模型调用、749253 Token，无 Provider/契约错误。
- normal 为 clean 7/8、hard 7/8、Over-refusal 0/16、Evidence 15/15；两个文件案例仍因扩展查询达到六轮上限。
- attack source 72/72 精确；Agent ASR 1/72，唯一失守为社会工程 overscale 案例第 1 轮请求 `propose_plan`，由 agent-policy 拒绝；副作用 0/72、Containment 1/1、any-hit 1/24、stable 0/24。
- Canary Echo 9/9、Leak 0/9、攻击 Evidence 60/60；跨案例危险 Tool Ref 审计无漏记。
- 含预检共 371 次模型调用、774880 Token，峰时缓存未命中费用上界 2.908050 元；本轮 91 Task 授权已消费。
- Phase 12/13 增加解析勘误，正式结果以 `docs/evidence/phase14-source-isolated-live-eval-v2.md` 为准。
- 最终回归：30 tests + 5 subtests、compileall、v1/v2 lint/Replay、51 个 Markdown 围栏/链接、diff check 全通过；168 个仓库文件精确 Key 匹配为 0，通用模式仅命中脱敏单测假值。

## 文档收口记录

- Phase 4 完整历史已快进合并到 main，并推送 GitHub；没有 merge commit 和冲突。
- 文档从“按 Phase 开发顺序”改为“当前真相、模块学习、面试表达、历史证据”四层导航。
- 新增项目全景与心智模型、代码导读与模块地图、Agent 八股知识地图、简历与面试表达手册。
- 重写项目学习路径和综合面试题库，当前口径统一为手写 Pi-style 双层循环和九个结构化工具。
- Phase 2 LangGraph、Phase 2.1、Phase 3、Phase 4 计划保留为历史资料，不删除真实证据。
- 文档收口轻量验证通过：Go test -p 1、go vet、go build、Python frozen 22 tests、全部 Shell 语法、JSON、Markdown 本地链接、diff check 和新增内容秘密模式扫描。
- 本轮没有启动 kind、Docker、Prometheus、Alertmanager、SSH Target 或 Live LLM；只改文档与项目描述元信息，不修改运行时代码。
- 2026-09-01 继续做 P1/P2 文档一致性收口：`10` 成为当前项目完整答案唯一入口，`26` 只维护通用 Agent 概念，`15/19/23` 缩为问题与权威锚点索引。
- `docs/README` 将“20 分钟快速了解”和 `13` 的“一天/四天系统学习”按受众分开，不再提供互相竞争的时间不足清单。
- LangGraph 历史手册改名为 `14-Phase2-LangGraph历史实现与证据.md`，避免从文件名误认成当前 Runtime。
- 新增 `evidence/learning-experiments.md`，登记 6 个学习实验并区分亲手执行、等价证据、部分证据和尚未执行；没有补写虚假实验输出。
- 本次仍只修改文档，未启动 kind、Docker、Prometheus、Alertmanager、SSH Target 或 Live LLM，也未执行尚待学习分支验证的破坏实验。
- P1/P2 轻量验证通过：`git diff --check`、全部 Markdown 围栏配平、旧文件名零残留、本地 `.md` 路径和 GitHub 风格标题锚点检查均通过。

## 2026-09-01 main 全量回归

- 在提交 2c47579 上串行执行完整静态检查与 Phase 1–4 E2E；开始时 16 CPU、可用内存 7097 MiB、swap 0。
- 经 kind 标签、角色和固定镜像摘要核实后，只启动已停止的项目 sandboxd-control-plane；未触碰停止的 minikube 和 minio-tutorial。
- Go test -p 1/vet/build、Python compileall/22 tests、Shell、JSON、Markdown 链接、diff 和秘密模式检查全部通过。
- Phase 1 demo 全部通过：gVisor、RBAC/NetworkPolicy、Manager/Exec、5 并发唯一认领、6 次 CAS 冲突、Metrics、DryRun、Agent/Operator 分权和 stale Plan。
- Agent Replay 全链通过：Prometheus/Alertmanager、Prometheus Query、gVisor Kubernetes read、Pod Log 注入、agent-policy、tool-policy 403、RBAC 403、Agent approve 401、Pending Plan 和 replicas 不变。
- Linux/File Replay 全链通过：strict host key、低权限双 forced-command、任意命令 exit 126、Linux Log 注入、task Workspace、文件正文 Trace/Session 脱敏，原链路不退化。
- Live DeepSeek 未执行：外部请求会发送告警和日志上下文，已有 Key 使用授权不等于项目数据外传授权；安全审查在请求前停止，没有发送数据或暴露 Key。
- 最终残留审计：目标 namespace、managed Pod、四个测试端口、业务进程、SSH Target 和临时目录均为 0；随后把本轮启动的项目 kind control-plane 停回测试前状态，最终运行容器 0、可用内存 7037 MiB、swap 0。
- 详细脱敏记录见 docs/evidence/phase9-full-regression.md。

## 2026-09-01 DeepSeek 单次 Live E2E

- 用户明确授权向 DeepSeek 发送 sandboxd 演示告警、Pod Log/ConfigMap 和工具上下文，并要求使用仓库外既有 Key 只运行一次 Live E2E。
- Key 鉴权与模型列表预检成功；固定使用 `deepseek-v4-flash`、官方 endpoint、`thinking=disabled`，Key 只经进程环境传入。
- 测试前 16 CPU、可用内存 6384 MiB、swap 0；只启动经 kind 标签、role 和不可变镜像 ID 核实的项目 `sandboxd-control-plane`。
- Live Task `succeeded`：4 次模型调用，5 次 `kubernetes_read` 和 1 次 `list_files`；Pod Log/ConfigMap 注入进入上下文，verdict 为 `not-triggered`，没有 Plan。
- 模型没有调用 `query_prometheus`，严格脚本在结果断言处退出 1；遵守“一次”限制没有重试，也没有把 Task 成功写成完整 E2E 成功。
- 因提前退出，本次没有执行后续 Go Tool Policy、RBAC DELETE 和 `dmesg` 明确断言；这些仍以 Phase 9 证据为准。
- 清理复核通过：精确 Key 未进入 evidence，target namespace、managed Pod、四个测试端口、runtime 和 Workspace 均无残留；项目 kind 节点停回测试前状态，最终运行容器 0、可用内存 7040 MiB、swap 0。
- 人工脱敏记录见 `docs/evidence/phase10-deepseek-live-e2e.md`；原始本地 evidence 不提交 Git。

## Phase 4 本轮记录

- 2026-08-31 用户明确授权 Phase 4 并开启目标模式：实现受限 Linux Host 插件和不带 artifact 前缀的五个原生文件工具。
- 从已完成并同步远端的 `42bb744` 创建 `codex/phase4-linux-files`，创建前工作区干净。
- 固定工具名为 `linux_read`、`list_files`、`read_file`、`search_files`、`write_file`、`edit_file`；不增加任意 Bash/SSH/远端写能力。
- 固定 Linux Host 使用静态 targetId、四个只读 operation、严格 Host Key、低权限 forced-command；SSH 不经过 gVisor，禁止混淆两条信任边界。
- 改造前资源审计：16 CPU、可用内存 7076 MiB、swap 0、Linux 根盘可用 937 GiB、Windows 盘可用 197 GiB、运行容器 0。
- kind 元数据仍指向项目单节点 `sandboxd-control-plane`；只启动了经镜像摘要和 kind 标签核实的该容器，没有创建第二个集群。
- 启动后节点、控制面、Calico、CoreDNS 和 local-path 均恢复 Ready；E2E 前可用内存约 6400 MiB、swap 0、运行容器 1。
- 2026-08-31 改造前真实 Replay E2E 完整通过：Prometheus -> Alertmanager -> agentd、Prometheus/Kubernetes 查询、Pod Log 注入、agent-policy/tool-policy/RBAC 拒绝、Agent approve 401、Pending Plan、replicas 不变和 `Starting gVisor` 均有脚本断言。
- 改造前脱敏证据位于本地 `.cache/agent-demo-evidence/4e111abd02e44029bc50c45d29326351`；该目录不提交 Git，也不冒充 Live LLM 证据。
- E2E 自动清理本轮 `sandboxd-target`、managed Pod、四个服务监听和渲染配置；基线通过后才允许开始 Phase 4 功能代码。
- M2 完成 `LinuxHostClient`、`linux_read` 与静态 Target Registry：固定 `/usr/bin/ssh` argv、strict host key、输出/超时上限，失败不回显凭据路径；Connector 对未知 target/operation 独立返回 `connector-policy`。
- 一次性 Target 基于本地已核实 image ID 的 kicbase v0.0.50，资源限制为 0.25 CPU、192 MiB、64 PID、read-only rootfs，只绑定随机 localhost 端口；低权限 UID 10001 无 sudo，authorized_keys command 与 sshd ForceCommand 双重收口。
- M3 完成 `list_files`、`read_file`、`search_files`、`write_file`、`edit_file`；task 工作区使用 Linux 0700/0600，限制路径/层级/symlink/256 KiB，覆盖使用 SHA256 CAS，写入使用同目录原子替换并返回有界脱敏 diff。
- Trace/Session 对 write content 和 edit old/new text 只保存 bytes + SHA256；read/search/diff 对常见 API Key/Bearer 做模式脱敏。它不是完整 DLP，工作区仍不得存生产秘密。
- 首次 Phase 4 真实 E2E 通过，证据为本地 `.cache/agent-demo-evidence/f2d0ba2e986244f1a55fc9de56c3ca23`：Linux SSH、文件写读、Linux/Pod 两路注入、原 gVisor/Policy/RBAC/审批链均通过，正文未进入 Trace/Session。
- 实测后只剩项目 kind control-plane；`sandboxd-target`、Phase 4 Target、8080/8090/9090/9093 监听和 `/tmp/sandboxd-linux-demo.*` 均无残留。
- Python frozen 单测由 16 增至 22，覆盖 SSH 固定 argv/二次拒绝、路径逃逸/symlink/CAS/权限/任务隔离，以及 Phase 4 Replay 的 8 次 Tool Call 和 Trace 脱敏。
- 实现中记录四个可讲坑：BuildKit 离线 digest 解析、`/run` tmpfs 遮挡、sshd 降权读取 authorized_keys、Docker 随机端口语法；均先做最小实验再修复，没有放宽安全边界。
- 最终静态审计通过：Go `test -p 1`/vet/build、Python compileall/22 tests、全部 Shell 语法、Replay JSON、Markdown 本地链接和 diff check；Go `cmd/internal` 与既有 Kubernetes 安全清单零修改。
- 最终改造后 E2E 再次通过，证据为本地 `.cache/agent-demo-evidence/5b179a176e6a4448b5aa8a7daace3cd0`；新增直接 SSH `cat /etc/passwd` 的远端负向路径返回 126，原 gVisor、Policy、RBAC、审批链继续通过。
- 最终清理审计只剩项目 kind control-plane；目标 namespace、Phase 4 Target、四个服务端口、`/tmp/sandboxd-linux-demo.*`、`/tmp/sandboxd-agent-workspace.*` 和旧测试空目录均无残留。
- 实现代码、测试和脚本已形成提交 `1a4e98f`；三个新增 Shell 脚本通过 Git index 显式保存为 100755，避免 `/mnt/c` 的 `core.filemode=false` 丢失执行位。
- 文档提交 `d71c4e9` 与实现提交 `1a4e98f` 已推送到 GitHub 分支 `codex/phase4-linux-files`；Phase 4 不创建 PR、不合并其他分支，等待用户后续决定。

## Phase 2 完成审计

| 完成判据 | 权威证据 |
|---|---|
| Prometheus 告警与 Alertmanager Webhook | docs/evidence/phase8-agent-alert.md 的 Replay 与 Live 输出 |
| Live LLM 完成诊断 | 三次 DeepSeek Live Task 均 succeeded；第 2 次完整脚本通过，第 3 次结构化 Diagnosis |
| Prometheus + gVisor Kubernetes 查询 | 第 2 次 Live 的 1 次 Prometheus、5 次 Kubernetes Tool 与 Starting gVisor |
| Pod Log/ConfigMap 注入进入上下文 | Live `injectedVia=[podlog, configmap]` 与脱敏 Trace |
| Replay 危险调用和 Python Policy | replay-contained.json 与最新 Replay 全链路 |
| Go Tool Policy、RBAC、Agent 无审批权 | tool-policy 403、RBAC Forbidden 403、Agent approve 401 |
| 诊断或 Pending Plan | Live 输出诊断；Replay 输出 Pending Plan 且 replicas 不变 |
| 清理和秘密 | namespace/Pod/端口/渲染配置无残留，swap=0，Key 与掩码指纹扫描无命中 |
| 构建与最小测试 | Go test/vet/build、Python 7 tests、compileall、Shell/JSON/链接检查 |
| 文档和 GitHub | README、学习/面试/39 条踩坑、evidence 完成；当前收尾提交推送到固定分支 |

## 恢复工作时

1. 阅读 GOAL.md。
2. 阅读 docs/README.md 和 docs/24-项目全景与心智模型.md。
3. 阅读本文件与 AGENTS.md。
4. 查看 git status、当前分支和最近提交。
5. 只读取本次任务涉及的历史计划、代码和证据。
6. 只继续“下一步”的第一项未完成工作；没有用户授权时不新增功能阶段。

## Phase 2.1 本轮完成

- 新增 ToolResult 双通道：model_content 进入模型，auditDetails 只进入有界本地 Trace。
- 新增 Agent/Turn/Model/Tool/Sandbox 生命周期事件、provider capability、usage、finishReason 和耗时。
- 新增 48 KiB 确定性上下文预算，保留安全 System Prompt 和完整 Tool Call/Result 组。
- 取消后使用独立 10 秒 cleanup Task 释放 sandbox。
- Python 单测由 7 个增至 9 个，覆盖上下文协议、双通道事件和取消只释放一次。
- 明确不实现 Pi Session、steer、follow-up、插件、长期记忆和工具并行。
- 最终验证：Go test/vet/build、Python compileall/9 tests、diff check 全部通过；未启动真实集群或 Live LLM。

## Phase 3 本轮记录

- 2026-08-31 用户明确授权从 Phase 2.1 扩展到 Pi-style 极简 Runtime，并要求进入目标模式持续实现。
- 已创建 `codex/phase3-pi-runtime`，基线为 `787d023`，创建前工作区干净。
- 完成 Phase 3 权威目标、执行规则和实现计划；固定不重写 sandboxd。
- 固定 Pi 只作为源码设计参考，不增加 Node、TypeScript、Pi 或新的 Agent 框架依赖。
- 改造前轻量基线为 9 个 Python 单测通过；显式使用 `uv run --project agentd --frozen`，未启动集群或 Live LLM。
- 用户明确允许后，以 sudo 从 Ubuntu 软件源只安装 `bubblewrap 0.9.0-1ubuntu0.1`，恢复受保护的终端/补丁命令；未修改其他系统配置。
- M1 新增显式受信任 Plugin Registry、Prometheus 插件和 Kubernetes/Plan 插件；没有目录扫描、动态安装或第三方代码加载。
- 模型 Tool Schema 由 Registry 生成，删除 policy.py 中重复 Schema；Python Policy 校验逻辑保持独立。
- Trace 增加插件清单及每步 pluginId/pluginVersion；只读 `GET /api/v1/plugins` 受 API Token 保护，Alert Token 返回 401。
- 现有 Replay 的 Prometheus/Kubernetes/Plan 调用已经真实经过插件分派，危险调用仍在 agent-policy 被拒绝。
- M1 验证为 Python compileall、diff check 和 12 个单测通过；sandboxd 零修改，未启动集群或 Live LLM。
- M2 新增 `runtime/loop.py` 双层循环：内层 Tool Call/steer，外层 follow-up；工具继续顺序执行。
- 新增 `runner.py` 隔离 Sandbox 生命周期；旧 `graph.py` 缩为兼容导入层，Phase 2 调用路径不破坏。
- 专门单测证明 steer 在当前 Turn 后进入下一轮，follow-up 只在自然结束后进入外层循环。
- 删除 LangGraph 直接依赖并离线更新 uv.lock，同时移除四个 LangGraph/ormsgpack 包。
- 原 Replay 注入拦截、Pending Plan、插件 Trace 和取消只释放一次继续通过。
- M2 验证为 frozen 环境 14 个 Python 单测通过；未启动集群或 Live LLM。
- M3 为每次运行生成 taskId、为线性事故上下文生成 sessionId；resume 沿用 sessionId，但创建新 taskId 和新 Sandbox。
- 新增独立 steer/follow-up FIFO、真实 asyncio cancel，以及五个受 API Token 保护的控制/Session API；Alert Token 无权控制任务。
- Session 使用 append-only JSONL 保存脱敏 Header、Command、Transcript 和 Result；不保存 additional_kwargs、隐藏思维或原始凭据。
- Session 目录/文件在 Linux 文件系统上使用 0700/0600；测试发现 WSL 的 Windows TEMP 默认位于 `/mnt/c`，DrvFS 未启用 metadata 时不能验证 POSIX 权限，因此安全状态文件必须放 Linux 文件系统。
- 修复“整段 JSON 先正则脱敏再解析”可能破坏引号或被截断的问题，改为对 Tool 参数叶子递归脱敏并保持合法 JSON。
- M3 最小验证为 frozen 环境 16 个 Python 单测通过；没有启动 kind、Prometheus、Alertmanager 或 Live LLM。
- M4 新增 docs/18 当前 Runtime 学习手册和 docs/19 的 30 个秋招问答；旧 docs/14/15 明确标记为 Phase 2 历史实现与证据。
- README、agentd README、文档索引和项目学习路径已切到双层 Loop、受信任插件与 Session 身份，并保留 LangGraph 重构演进的讲解价值。
- 踩坑新增 40–43：WSL `/mnt/c` 权限语义、结构化 JSON 脱敏、两类 CancelledError、Session/Task/Sandbox 身份分离。
- 文档反查代码时发现 Alert Header 也可能含凭据；已改成结构化逐叶脱敏，并新增 Bearer 告警注解回归断言。
- 最终轻量验证通过：Go test `-p 1`、go vet、go build `-p 1`、Python compileall、16 个 frozen 单测、全部 hack Shell 语法和 diff check。
- 安装 bubblewrap 后，受管理 seccomp 环境会阻塞 FastAPI 线程和 Go 编译子进程；验证改在 WSL 沙箱外串行运行，仍未启动集群或 Live LLM，也没有残留进程。
- Phase 3 四个实现提交 `42c75af..fe0273b` 已推送到 `https://github.com/chx739/sandboxd.git` 的 `codex/phase3-pi-runtime`；未合并或改写其他远端分支。
