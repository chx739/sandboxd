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

这些均为后续必须实现项，当前后端组件代码没有完成整个协议。

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

当前只运行kind控制节点、NFS和专属备份MinIO；组件业务/维护Pod已清理，所有工作区、快照和Secret保留。没有Session联合绑定、元数据提交/崩溃恢复、分支恢复及引用GC证据前，不能宣称V2完成。全部十项正式验收仍pending，机器可读范围及证据hash见`evidence/phase9-v2-progress.json`。

### 持久屏障模块与可信备份执行器

`internal/sandbox/workspace_barrier.go`与owner共用Go独占、fsync的JSONL。预留屏障与文件RPC使用同一短锁，先等待已在执行的RPC返回；随后长期扫描/上传不占这把锁。状态为reserved→submitted→identified→stopped→released，记录checkpoint/job/owner/代次和维护Pod UID。Files和Claim在屏障未释放时均拒绝；取消仍可停止业务owner，但无法绕过屏障换新Pod。

提交API请求前先记submitted；请求结果不明或Pod消失不等于从未提交。只有从未提交的reserved，或可信Runtime确认同UID容器终止后，才能进入stopped，再解除。旧状态的精确CAS不能解除新job。注册表重开时同时重放屏障，非法状态迁移即使JSONL校验和合法也拒绝启动。

8项定向测试通过；全项目Go测试通过`phase9-v2-go-full-regression-tests.txt`，六包race回归见`phase9-v2-barrier-regression-tests.txt`。该批为逻辑证据；后续真实执行器探测见下节，Agent接线仍待实现。V1旧记录保持可读；写过V2屏障的控制目录不支持旧二进制继续写入，旧reader拒绝未知事件。

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

目前仅接可信Go备份路径，未接HTTP/Agent联合检查点、restore写屏障、Session父链引用或COMMITTED日志；不能把组件本地backend-result文件当成联合检查点提交。V2十项完整验收与性能比较均保持pending。

## 5. 官方参考与版本

- [restic 0.18.1 release](https://github.com/restic/restic/releases/tag/v0.18.1)：固定二进制与校验表。
- [0.18.1 MinIO/S3配置](https://restic.readthedocs.io/en/v0.18.1/030_preparing_a_new_repo.html#minio-server)：S3兼容仓库、凭据、路径式访问；初始化可创建专用bucket。
- [0.18.1脚本接口](https://restic.readthedocs.io/en/v0.18.1/075_scripting.html)：JSON结果与退出码。

二进制本身的`restore --help`也确认snapshotID:subfolder语义；版本化文档无法通过浏览器加载的恢复页面不当作已读取证据。
