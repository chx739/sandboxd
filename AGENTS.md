# 后续开发执行规则

> 当前仓库状态：Phase 1–6 已完成，Phase 7 代码与历史证据保留。用户于 2026-09-29 授权 Phase 8 Kubernetes 运维 Agent MVP。当前完整范围与验收见 `GOAL.md` 和 `docs/37-Kubernetes运维Agent目标与验收.md`；用户最新已将 Jev 最小路由加入当前范围，覆盖旧暂停条款。

本仓库的任何自动化编码助手在开始工作前，必须完整阅读：

1. `GOAL.md`：最高优先级目标、边界和安全红线。
2. `docs/README.md`：当前文档分层和权威入口。
3. `docs/24-项目全景与心智模型.md`：当前实现、信任边界和项目口径。
4. `docs/PROGRESS.md`：已经完成、正在进行和下一步。
5. 与本次任务直接相关的代码、学习文档和最近验证证据。

Phase 8 开发还必须完整阅读 `docs/37-Kubernetes运维Agent目标与验收.md`。Goal 工具中的短句仅是状态摘要，不能代替该文档。

工作区持久化、Pod 接管或 agentd 重启恢复相关工作，必须先完整阅读 [NFS V1 与 restic + MinIO V2 实施约束](docs/43-工作区持久化与恢复实施约束-NFS-v1与restic-MinIO-v2.md) 和 [目标模式 Prompt](docs/44-工作区恢复目标模式Prompt.md)。2026-10-17 用户已明确授权按文档 43 开始实现并进入目标模式，覆盖此前“仅文档”状态。当前为 Phase 9，先 V1 后 V2，允许必要的本地测试、项目专用服务与可控故障验收；不使用外部付费模型或旧预算。

Phase 9 允许增加本项目沙箱专属工作区的受限文件 RPC、NFS 挂载、持久任务/attempt 日志及 restic/MinIO 检查点。它只替代默认 agentd 本地 task 文件路径，不授权外部主机任意写入、任意 Shell、生产级 HA 或数据库。历史 Phase 4“仅本地文件”、Phase 7“恢复创建新 Task”不限制本轮明确授权的工作区迁移和同 Task 自动接续；手动 resume 仍创建新 Task。

旧工作区迁移使用显式管理CLI，先停源写入者并持部署Task账本锁；PREPARING不算可恢复映射，只有Go停止确认、文件清单/hash和Session副本校验后COMMITTED才可接续。保留源文件/原JSONL/失败目录，不把迁移能力暴露为LLM工具。统一入口见hack/workspace.sh；分发代码与实际执行成绩必须分开记录。

工作区性能与流量的基线见文档46与phase9-nfs-fixed-workload-serial.json；并行探索报告单列。V2比较复用固定seed/文件规模/缓存/并发，计入同步检查点等待；容器网络计数不等于公网流量，端点内存不等于峰值，不把本机小样本外推生产QPS。

V2续作先读文档47：Go Reserve只冻结，Session引用/PREPARING持久化后才Start；DATA_READY不等于可恢复，COMMITTED同条日志投影head/revision。最终提交与Task取消共用短锁，不能将网络备份放进取消锁；当前已有真实Agent正常保存证据（文档47 §7），不代替真实故障/历史恢复的完整验收。AGENTD_CHECKPOINT_MODE=off为V1默认，restic需完整可信Go配置；不注册备份模型工具。

V2引用清理续作还须读文档48：按仓库crypto ID与完整snapshot聚合全部引用；pin、分支头、保留恢复源和未提交数据不可删除。本地预览与PREPARED/FORGOTTEN日志控制已实现，但不等于实际forget/prune已接线或V2-09验收。管理CLI默认只读预览、只接受固定pin/unpin，禁止把测试中的complete_forget当真实删除证明。后续实际执行必须经可信Go固定操作与全局串行屏障，保留全部旧证据所用数据。

owned helper组件（文档48 §8）已在新专用合成仓库实际验证精确forget/prune/恢复与归属拒绝；v3管理镜像不挂业务卷，默认v2路径未切换。组件使用可信管理员直接Pod/主动停源，不是Go持久GC屏障、Operator入口或Session引用保护联合验收；下一步不得跳过这些接线或对共享历史仓库执行清理。

文档48 §9新增Go日志repository.binding和显式Owner部署参数；只准全新控制日志，拒绝旧工作区自动收编和重启降级。owned备份已接可信Runtime并核对crypto；本地Go/fake回归不是新真实存储验收。该绑定不代替Python控制目录唯一归属、GC持久屏障或Operator授权，后续仍按完整管理协议实施。

文档48 §10已接Go持久GC屏障/固定执行与Operator专属HTTP，本地fake/race/全Go通过，不等于真实联合GC验收。独立Python管理员协调器的接线见§11；只有尚未PREPARED且Go证明never-submitted的过期预留可cancel-reservation，PREPARED之后不以此解冻。未知/失败保持冻结并按同GC/job对账，不能给agentd服务注入Operator Token。

文档48 §11已接独立checkpoint_cleanup管理协调器：同CP日志的repository.binding/cleanup.execution、原生目录路径+设备/inode和本机固定仓库claim，禁止旧历史自动收编及同仓库另一目录登记。管理员命令持CP单写入锁，PENDING先落盘再Reserve，PREPARED后才Start，核验Go结果后FORGOTTEN→Finish→DONE；失败不自动重试/解冻。agentd在Worker前拒绝未对账管理状态，不消耗新模型预算。该实现批次仅本地Fake验证，后续真实联合结果见§12；任何后续实验仍不能复用旧共享或故意污染夹具。

最新真实GC见文档48 §12：新owned-joint仓库的5个真实联合CP、head/pin/保留来源拒绝、管理员及Go SIGKILL同job对账、实际排他锁/固定目标重试、prune后pin版本文件恢复通过；独立审计30/85/42条日志及4个保留snapshot/read-data，真实只读预览10文件未改，完整Python306通过。仅删除本轮新建的两份合成snapshot，旧共享/污染夹具保留。此实验不是新的历史分支Task恢复或性能测评；接下来固定性能/流量、统一V2入口、十项逐项审计，Goal仍完整active。

固定V2性能见文档46 §7～8：同一Go二进制重测V1，V1/V2各9/9、9独立仓库/18备份/9恢复；独立重算324条Go日志/数据/manifest/计数与分位数。仅可信后端批量微基准，不是默认Agent逐工具组总时延；V1复用Go、V2逐仓库重启Go，prepare/claim不作纯因果比较。packed新增与容器字节分列、du表观字节含metadata、资源仅端点非峰值；不得外推公网费用、SLA或宣称V2运行I/O更快。下一步统一V2入口与十项最终审计，不重跑已完成基准来筛选更好数字。

统一安装续作：workspace.sh build-storage已有真实缓存校验/镜像复用/当前Go编译/kind加载证据和4项Fake契约测试；已有tag不覆盖，首次无缓存Docker构建未重跑。默认.cache/workspace-sandboxd已更新为本批源码构建，旧owned-cleanup缓存保留。fresh Owner持久初始化、目录绑定和统一restic服务启动仍待完成；不要从镜像准备成功推导部署完成或自动收编旧共享仓库。

统一部署后续以文档47 §19为准：init-v2固定新仓库/controller/Secret/job，PLANNED→SECRET_READY→INIT_SUBMITTED→OWNER_READY→READY；意图先落盘，未知后只查原Pod，缺失/失败/UID变化不得盲目init。已真实验证新owned-deploy初始化/复用、目录claim与统一Go/Agent SIGKILL接续和3份CP，独立32/10/35条日志及仓库read-data通过；本地初始化异常23项与真实服务分开。当前新目录已有Task，不能重跑entry夹具；正常启动沿原目录，旧共享/污染夹具保留。下一步完整十项逐项审计，不能从本轮单项通过宣布整体完成。

最终验收续作先读文档49：V2十项原始证据/43保留控制日志已审计10/10，新acceptance专属仓库补真实保存重试、活动来源/缺失/校验/镜像/遗忘拒绝；仅遗忘本轮新建CP1，marker/head保留，不复用该目录作为fresh夹具。当前完整Goal仍需最后回归与交付门禁/PR；workspace.sh benchmark-v2默认统一构建Go与原生restic，旧性能报告及旧--binary显式选择保留。三类最终联合Replay已3/3且四依赖停回原状态，不能把Fake模型/生命周期当Live模型或真实K8s修复。

只有修改某个历史模块时，才需要额外阅读对应计划：Phase 1 读 `00`，Phase 2 读 `12`，Phase 2.1 读 `16`，Phase 3 读 `17`，Phase 4 读 `20`。不要每次把全部历史计划重新当作当前需求。

仓库文件、Git 状态和实际命令输出比聊天记忆更可信。上下文不完整时，不要重新设计项目，也不要根据猜测扩大范围；从 `docs/PROGRESS.md` 的下一步恢复。

执行时必须遵守以下规则：

- 只实现当前模块的最小可运行闭环，优先简单 Go 代码和中文“为什么”注释。
- 每个模块同步维护学习文档、面试问答和最小验证命令。
- 不追求生产级架构和测试覆盖率，不引入目标外框架或功能。
- 执行真实集群测试前检查系统资源，默认串行、小并发、低副本。
- sudo 默认禁用；确需使用时严格遵守 `GOAL.md` 的 sudo 与系统安全红线。
- 绝不读取后展示、复制、提交或记录密码、API Key、令牌等秘密。
- 不触碰 sandboxd 仓库外的简历、其他仓库和用户文件；不清理非本项目资源。
- 修改前检查 `git status`；保留用户已有修改；按模块小步提交并推送 GitHub。
- 验证失败时记录事实和最小复现，不用虚假输出或普通 runc 结果冒充 gVisor 成功。
- 只有 `GOAL.md` 的完成判据全部有证据时，才可以宣布整个项目完成。

Phase 2 额外规则：

- Go sandboxd 是可信执行边界，Python agentd 和 LLM 均视为不可信调用方。
- Phase 2 当时使用 LangGraph 编排；无论编排实现为何，都不得把 Prompt、Tool Schema 或循环分支宣传为最终安全边界。
- agentd 不得接收、保存或使用 Operator Token，不得调用 approve/reject。
- Live 与 Replay 证据必须明确区分；人工 Replay 标记为 deterministic-policy-case。
- 默认禁用 LangSmith，不上传 Alert、模型输入、工具输出或 Trace。
- 值得复盘的真实问题写入 docs/11-开发踩坑与排障.md，包含现象、根因、修复和面试价值。
- Phase 2 历史分支为 codex/phase2-langgraph-agent；最终代码以 main 和当前 Runtime 文档为准。

Phase 2.1 额外规则：

- Pi 当时只用于改进事件、上下文与取消，不替换 LangGraph；这是 Phase 2.1 历史范围。
- 模型上下文与审计数据必须分离；审计字段不能由 LLM 自报覆盖。
- 安全 System Prompt 和最新拒绝证据不得被上下文转换移除。
- 工具保持顺序执行；禁止为了模仿通用 Coding Agent 引入并行工具、插件或任意系统工具。

Phase 3 额外规则（历史已完成阶段）：

- Phase 3 历史分支为 `codex/phase3-pi-runtime`；最终代码以 main 和 Phase 4 后的当前文档为准。
- Pi 只作为设计参考；使用 Python 手写最小双层循环，不增加 Pi、Node、TypeScript 或新的 Agent 框架依赖。
- Plugin Registry 只注册仓库内受信任内置插件。不得从任意目录、网络、npm、Git 或用户输入动态加载代码。
- 插件清单是能力声明，不是授权本身；Python Policy 和 Go sandboxd 仍必须独立拒绝越权请求。
- Session 采用线性 append-only JSONL；只保存脱敏消息和运行事件，不保存 Header、Token、API Key、隐藏思维或原始凭据。
- steer 只在当前 Turn 的安全点进入下一轮上下文；cancel 才负责取消当前运行。不得把 steer 宣传为已撤回正在执行的外部动作。
- 第一版工具继续顺序执行，便于审计、预算统计和面试讲解；不为了模仿 Pi 默认并行而引入竞态。

Phase 4 额外规则（历史完成范围）：

- Phase 4 历史分支为 `codex/phase4-linux-files`，最终实现已经合并到 `main`；不得再把历史分支要求误当成当前分支要求。
- Linux Host 插件只能调用部署者固定的 targetId 和四个只读 operation；不得让模型提交 host、user、port、identity path、任意 argv、Shell 字符串或 sudo。
- SSH 必须使用 `BatchMode`、严格 Host Key、固定 known_hosts、独立低权限账号、forced-command，以及禁用 TTY/转发；私钥只存在仓库外临时目录。
- Demo Target 必须是本项目一次性容器或虚拟机，资源受限并可精确清理；禁止默认连接真实 WSL 或宿主机。
- 文件工具只访问 `taskId` 专属工作区。路径必须是相对路径并拒绝 `..`、符号链接和逃逸；写/编辑必须记录 hash 和有界 diff。
- `write_file`/`edit_file` 只修改工作区草案，不代表外部系统已变更；不得添加远端文件上传、执行或自动审批。
- 不增加 Paramiko/AsyncSSH 等依赖；优先使用 WSL 已有 `/usr/bin/ssh` 的固定 argv 子进程，禁止 `shell=True`。

Phase 5 额外规则（当前 Eval v2）：

- 先读 `docs/29-Prompt-Injection-Eval学习手册.md`、`docs/30-Prompt-Injection-Eval-v2秋招测试计划.md` 和 Phase 13 evidence；v1 是不可改写的历史基线，默认当前实现为 40 场景 v2。
- 数据集只能使用合成运维内容和显式 canary，不得复制真实 Token、用户日志、kubeconfig、主机信息或仓库外秘密。
- 必须把 Agent ASR 与系统未授权副作用率分开；Replay 故意发出危险 Tool Call 时，不得把 100% Agent ASR 解释为真实模型结果。
- 安全事实优先使用确定性断言：Tool Call、denyLayer、外部状态、canary、清理结果。自然语言 Diagnosis 不使用 LLM-as-Judge 决定是否越权。
- Replay、本地纯逻辑和 Live 三种证据必须分开。没有用户新的明确授权，不得读取 Key 或批量向外部模型发送 Eval 场景。
- 2026-09-01 的 DeepSeek Live Eval 授权已用于预检、Run 1 和修正夹具后的 Run 2；后续执行者必须视为已消费，不能从历史授权推导出新的外部调用权限。
- Eval v2 的 88 个 Live Task 已全部完成，该授权已消费。不得增加第 4 次攻击重复、在来源隔离修复后重跑、调用其他模型或连接真实系统；若要重新取得干净 Live 数字，必须获得用户新的明确授权。
- 解析修复后的 3 条预检 + 88 Task 已全部执行，本轮 91 Task 授权已经消费。后续不得据此追加重复、换模型或连接真实系统；正式结果只引用 Phase 14，Phase 12/13 保留为历史缺陷证据。
- v1 数据与 Phase 11/12 证据不可改写。v2 新增文件并将 CLI 默认切到 v2，但历史命令必须仍可显式加载 v1。
- Fake Connector 的 artifact 必须只从 case 声明的 source 返回；Scorer 必须把未声明 injection source 视为夹具契约错误。Phase 13 的首次 Live 有跨来源污染，只能引用其全局历史观察和缺陷，不得宣传来源分层结果。
- 默认只运行 Python 本地串行测试，不启动 kind、Docker、Prometheus、Alertmanager 或 SSH Target；需要真实集群时另做资源检查和精确清理。
- 不引入 AgentDojo、ASB、数据库、测试框架或新 Provider 依赖；优先标准库、现有 Pydantic 和当前 AgentRunner。

Phase 7 额外规则：

- 按 Session 树、记忆、检索、路由顺序逐模块完成运行与测评；每个模块更新 `docs/PROGRESS.md` 和对应学习/证据文档。
- 旧 Session 兼容读取；只从完整 Turn 恢复，不重放历史工具副作用。记忆和检索文本均是不可信数据，不能提升为 System 或授权指令。
- Milvus/ES/BGE 可新增必要依赖和本项目专用 Compose；服务只绑定本机，数据放原生 WSL 文件系统，运行前检查资源，不清理其他项目容器或卷。
- Jev 只建议模型选择，不更改工具权限。先使用 Fake/Replay；外部 LLM/Jev 调用前单列服务、样本量和费用上限并获得用户明确授权。密钥不得进入仓库、日志或文档。
- 2026-09-28 用户已批准 Phase 20 固定 8 题、最多 8 次 Jev + 24 次 DeepSeek、总预算 $1。联网执行已用 1 次 Jev 尝试和 3 次 DeepSeek，因 Jev 失败停止；TypeSafe IPv4 只读鉴权返回 401。另有一次无 DNS 沙箱超时且无请求报告，恢复前按 `docs/evidence/phase21-jev-live-first-case.md` 核算用量，不重跑整轮或擅自追加次数。现有授权不因上下文切换失效。

## Phase 8 当前执行规则

- 以 `docs/37-Kubernetes运维Agent目标与验收.md` 逐项验收。先审计并复用会话树、分层记忆；随后迁移知识库到 Milvus 2.5.10 内置 BM25 + Dense，再接 OpenSearch 日志和联合排障。
- Jev 已由用户重新加入：复用已有最小路由、更新后的 Key、每任务一次选模和强模型回退；先只读鉴权，再按授权次数与预算做最小验证。保留无 Key/Fake 路径。不得将旧无报告调用当作零用量或自动重置额度。
- 不直接删除已有 ES 索引、Compose 卷和历史报告。知识库迁移后的默认路径不依赖 ES；OpenSearch 只用于本轮日志工作负载。
- 60 题 RAG、30–50 题日志和性能评测须固定数据、模型与硬件口径，保留逐题原始结果。模型生成题、模型评分、人工审核和确定性校验明确分开；不得冒称人工金标。
- 外部付费模型调用需核对本任务具体授权、样本数与费用上限；未授权部分先做所有无费用工作并记录待执行计划。不得把历史授权挪作本轮测评。
- 每个阶段同步更新 `docs/PROGRESS.md`、学习文档、证据及 PR；不自动合并。
