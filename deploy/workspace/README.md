# NFS V1 部署与验收

当前状态：M4进行中，V1矩阵14/14、显式迁移与统一启动已通过真实验收；小规模性能基线9/9通过，V1单节点MVP具备交付证据，V2真实restic/MinIO后端及Agent联合检查点正常保存已验证，故障/恢复与正式验收尚未完成，见[47](../../docs/47-联合检查点与restic-MinIO学习手册.md)。逐项证据见docs/evidence/phase9-workspace-recovery.md与phase9-v1-acceptance-progress.json。

- 服务端：官方 Registry 在本环境超时，使用 nfs.Dockerfile 从固定 Debian 摘要和 Ganesha4.3-2 包构建，单独 Docker 容器，不运行动态 Provisioner。
- 协议：NFSv4.1/TCP 2049；客户端也必须明确指定 nfsvers=4.1。
- 持久数据：本项目 Compose 专属 workspace-data 与 nfs-state named volume；不复用 Milvus 的 MinIO 卷。
- 网络：仅 kind 网络，不发布 Windows/WSL 主机端口。
- 业务 Pod 只挂其 workspace_id 对应子目录；不得把整个导出根目录交给业务 Pod。
- 运行 UID/GID 65532、私有目录 0700、导出父目录0711，保留 root_squash，并将 anonymous UID/GID 显式映射为65532以兼容可信 gofer 的主机文件访问；不让模型传 server/export/path。

## 已确认的最小服务端权限

Ganesha VFS 的文件句柄访问需要额外 `CAP_DAC_READ_SEARCH`。该权限允许绕过文件/目录读取权限并调用 open_by_handle_at，因此属于可信存储服务权限，不能授给业务沙箱。

文档43 §11 要求扩大权限前询问用户；用户已于 2026-10-17 明确确认“允许该存储容器增加此权限”。授权仅限此处的 DAC_READ_SEARCH。保留 Docker 默认 seccomp，不配置 privileged、SYS_ADMIN、hostPID、hostNetwork 或 Docker socket；如实际测试还需要放宽 seccomp，先记录失败再单独评估最小变更，不静默切 unconfined。

## 运行前必须核对

1. 核对镜像实际 digest、二进制位置与版本，设置 `SANDBOXD_NFS_IMAGE=...@sha256:...` 或本机已构建的不可变 `sha256:<image_id>`；不要填未经核实的摘要。
2. 核对 Docker named volume 后端支持 Ganesha 的文件句柄系统调用，kind 节点支持 NFS client 与 mount.nfs。
3. 运行资源检查、创建本项目单节点 kind，检查 gVisor 与网络安全基线。
4. 按已确认的存储服务权限，实际测试 mount、锁、非 root 权限及持久化；失败就记录事实，不用本地 bind mount 代替 NFS 验收。

统一命令入口见本文末尾；缓存安装、Go构建、NFS复用、Go/agentd启动与重启接续已有真实证据。不要执行 `docker compose down -v`；Pod 删除与业务数据删除必须分开。

## Python部署默认值与真实验收入口

原有Agent/Alert Token与sandboxd Agent Token继续通过仓库外配置传入；不把Operator Token传给agentd。启动上面的Go服务后：

```bash
export AGENTD_WORKSPACE_BACKEND=nfs
export AGENTD_RECOVERY_DIR="$HOME/.local/share/sandboxd/agent-control"
agentd/.venv/bin/python -m uvicorn agentd.app.main:create_app --factory --host 127.0.0.1 --port 8090
```

环境读取函数默认nfs；control目录须在原生Linux持久盘，不能用/tmp。旧离线流程可显式配置`AGENTD_WORKSPACE_BACKEND=local`，只读旧Session可回退到旧trace目录；旧业务文件不自动搬迁；显式映射/校验迁移见本文后半部分，真实合成数据验证已通过。

下述管理夹具自行启动并结束本项目Go服务（固定127.0.0.1:10880需空闲），生成只用于本轮的合成Token，不读取真实Key；创建的NFS文件、PV/PVC和原生控制目录保留。它实际调用TaskStore/Runner与受限HTTP文件工具：

```bash
go build -o .cache/workspace-sandboxd ./cmd/sandboxd
agentd/.venv/bin/python -m agentd.workspace_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --output .cache/workspace-agent-file.json
agentd/.venv/bin/python -m agentd.workspace_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --fault-after-first-turn --output .cache/workspace-agent-pod-recovery.json
```

第二条场景在完整工具轮次落盘后由管理员正常删除本夹具的单个Pod，再由TaskStore自动接续；不force、不清空finalizer。文件读回、实际NFSv4.1、gVisor启动、UID/代次、停止证明及自然删除均验证。单次耗时不作为P50/P95/RTO结论；这不是强杀agentd测试。

进一步的真实进程与终态实验使用实际Uvicorn agentd子进程，固定端口18090也需空闲：

```bash
agentd/.venv/bin/python -m agentd.workspace_process_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --output .cache/agentd-sigkill-recovery.json
agentd/.venv/bin/python -m agentd.workspace_terminal_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --output .cache/agentd-terminal-restart.json
agentd/.venv/bin/python -m agentd.workspace_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --lose-write-ack --output .cache/workspace-write-ack.json
```

第一条只SIGKILL自己启动的agentd进程并验证OS退出码-9，用同一控制目录重启，无手动resume；第二条实际建立四类终态后重启。它们使用可选delayMs的固定Replay模拟在途模型响应，外部模型调用0；源码位于WSL挂载盘时启动时间较长，验收含这些等待，不能当生产SLA。第三条在真实写入成功后由夹具客户端丢弃确认，随后实际核查hash、不重放写入；不能将其描述为TCP断包实验。

原预算/截止时间与Go独立文件边界验收，仍须串行运行：

```bash
agentd/.venv/bin/python -m agentd.workspace_budget_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --scenario budget --output .cache/agentd-budget-restart.json
agentd/.venv/bin/python -m agentd.workspace_budget_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --scenario expired --output .cache/agentd-expired-restart.json
agentd/.venv/bin/python -m agentd.workspace_boundary_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --output .cache/workspace-boundary.json
```

过期场景按真实墙钟等待原120秒loop截止，再启动同目录服务，不修改时钟/账本。边界场景直接请求Go HTTP，不经过Python Policy；管理员只在自己的合成目录创建测试链接，验证拒绝后核对guard hash及链接保留。当前runtime-v1.5采用O_PATH目录描述符类型校验；原O_DIRECTORY组合与openat2候选的真实失败见踩坑53，旧镜像/报告保留，不以本地Linux单测代替gVisor结果。

并发与NFS故障实验也必须串行，先结束其他工作区任务：

```bash
agentd/.venv/bin/python -m agentd.workspace_concurrency_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --output .cache/workspace-concurrency.json
agentd/.venv/bin/python -m agentd.workspace_unavailable_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --output .cache/workspace-nfs-unavailable.json
```

第一条还需18091空闲，用不同端口验证同控制目录锁拒绝；旧attempt清理、新Pod可读和starting时取消分别核验。第二条要求sandboxd-demo/storage无其他Pod，仅暂停已核实的项目NFS容器约30秒，45秒一次性看门狗兜底恢复；正常finally先unpause再确认旧owner停止。Exec超时会回收旧Pod，恢复数据读取要在停止证明后认领新Pod。单轮故障时长不是日常延迟或HA指标；不会删除NFS文件/PV/PVC，不调用外部模型。

异常遗留时，核对报告中的controlRoot，只允许用本夹具原Go/Python账本恢复管理对账：

```bash
agentd/.venv/bin/python -m agentd.workspace_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --reconcile-control-root '<报告中的本夹具原生controlRoot>' --output .cache/workspace-reconciliation.json
```

此入口限制在`~/.local/share/sandboxd/workspace-agent-smoke/<run UUID>`，只通过Go停止已登记owner；不重放模型/写入、不删除工作区，不代替Agent自动恢复验收。

## Go 生命周期与 HTTP 探测

使用下文构建流程生成带两个helper的镜像，并加载到kind；新固定 `--check` 探测由 Go 绑定前使用。执行前核对本项目集群、当前NFS IP和可用内存。

```bash
go run ./hack/workspace-runtime-smoke -http \
  -nfs-server "$SANDBOXD_NFS_SERVER" \
  -image sandboxd-workspace:runtime-v1.5 \
  -control-dir "$HOME/.local/share/sandboxd/workspace-runtime-smoke" \
  -output .cache/workspace-http-rerun.json
```

历史runtime-v1/v1.1/v1.2/v1.3/v1.4/probe-v1与当前runtime-v1.5来自同源Dockerfile，旧tag和证据保留；当前命令统一使用runtime-v1.5。该探测创建合成内容，正常停止两个业务Pod和初始化Pod，保留工作区/PV/PVC。它验证Go账本重开和换Pod，不是强杀agentd自动恢复。

独立服务模式（预先按现有项目方式配置两个不同Token，Operator Token不传agentd）：

```bash
go run ./cmd/sandboxd -pool-size 0 \
  -workspace-nfs-server "$SANDBOXD_NFS_SERVER" \
  -workspace-image sandboxd-workspace:runtime-v1.5 \
  -workspace-control-dir "$HOME/.local/share/sandboxd/workspace-control"
```

可信维护命名空间/PV/PVC由Prepare创建，发现同名资源归属或NFS配置不匹配时拒绝，不覆盖其他资源。普通工作区Pod保留终止确认finalizer，正常回收必须经过工作区release接口；不要直接清空finalizer绕过终止证明。

## 组件探测复现

先完成原有 kind/Calico/gVisor 安全基线。下列只启动可信 NFS；用户已授权其最小权限。

```bash
docker build -f deploy/workspace/nfs.Dockerfile -t sandboxd-nfs:ganesha-4.3-2 deploy/workspace
export SANDBOXD_NFS_IMAGE="$(docker image inspect sandboxd-nfs:ganesha-4.3-2 --format '{{.Id}}')"
docker compose -f deploy/workspace/compose.yml up -d nfs

CGO_ENABLED=0 go build -trimpath -o .cache/sandbox-filetool ./cmd/sandbox-filetool
CGO_ENABLED=0 go build -trimpath -o .cache/sandbox-workspace-init ./cmd/sandbox-workspace-init
mkdir -p .cache/workspace-image
install -m 0755 .cache/sandbox-filetool .cache/workspace-image/sandbox-filetool
install -m 0755 .cache/sandbox-workspace-init .cache/workspace-image/sandbox-workspace-init
docker build -f deploy/workspace/sandbox.Dockerfile -t sandboxd-workspace:runtime-v1.5 .cache/workspace-image
kind load docker-image sandboxd-workspace:runtime-v1.5 --name sandboxd
```

M0 首次探测目录由管理员固定命令创建；这一步还不等于默认 Go 可信目录初始化已实现：

```bash
docker exec --user 65532:65532 sandboxd-workspace-nfs-1 mkdir -m 0700 /export/workspaces/workspace-00000000000000000000000000000001
export SANDBOXD_SMOKE_WORKSPACE_ID=workspace-00000000000000000000000000000001
export SANDBOXD_NFS_SERVER="$(docker inspect sandboxd-workspace-nfs-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
export SANDBOXD_WORKSPACE_IMAGE=sandboxd-workspace:runtime-v1.5
go run ./hack/workspace-fixture.go > .cache/phase9-workspace-fixture.json
kubectl --context kind-sandboxd apply -f .cache/phase9-workspace-fixture.json
kubectl --context kind-sandboxd -n sandboxd-demo wait --for=condition=Ready pod/sandbox-workspace-probe --timeout=60s
python3 hack/verify-workspace-nfs.py --output .cache/phase9-nfs-component-rerun.json
```

已存在的合成目录须先核对归属，不删除后重建；mkdir 因存在而报错时不要把所有错误都忽略。此低层夹具不登记Go账本，也不启用生产writer finalizer；使用独立storage-probe标签排除预热池。直接管理员exec只用于组件验收，不能宣传为Agent文件RPC或fencing验收；完整路线使用上面的Go生命周期探测。

依据：[Ganesha VFS](https://github.com/nfs-ganesha/nfs-ganesha/wiki/VFS)、[SIG Storage 服务端部署说明](https://github.com/kubernetes-sigs/nfs-ganesha-server-and-external-provisioner/blob/master/docs/deployment.md)。

命令接收与旧 attempt 审计恢复的独立真实验收：

```bash
agentd/.venv/bin/python -m agentd.workspace_command_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --output .cache/agentd-command-recovery.json
```

同样要求10880/18090空闲；只强杀夹具自己启动的agentd子进程。模型为固定Replay，不请求外部API；验证已确认的steer/follow-up重启后各注入一次、旧调用审计可查、终态再启动不改日志。不要与其他真实工作区夹具并发运行。

## 显式迁移旧agentd文件目录

先停止旧/新agentd及其他源目录写入者，保持Go sandboxd运行。`SOURCE`指旧`workspace-root/task-id`目录，`SESSION`指对应旧Session JSONL。命令不删除源文件；只支持现有文件工具能完整校验的UTF-8文本范围，超限/空目录/链接等明确拒绝。旧Task须为终态。

先预览（不访问外部服务）：

```bash
agentd/.venv/bin/python -m agentd.workspace_migrate \
  --source "$SOURCE" --session "$SESSION" --session-id "$SESSION_ID" --task-id "$OLD_TASK_ID"
```

核对返回的文件清单，将`manifestSha256`和`sourceSessionSha256`分别放入下方参数。Agent Token通过现有仓库外环境`SANDBOXD_TOKEN`传入，不使用Operator Token：

```bash
agentd/.venv/bin/python -m agentd.workspace_migrate \
  --source "$SOURCE" --session "$SESSION" --session-id "$SESSION_ID" --task-id "$OLD_TASK_ID" \
  --control "$AGENTD_RECOVERY_DIR" --sandboxd-url "$AGENTD_SANDBOXD_URL" \
  --expected-manifest "$MANIFEST_SHA256" --expected-session "$SOURCE_SESSION_SHA256" --apply
```

COMMITTED后启动agentd，再用原resume接口手动接续最新节点；新Task接回导入工作区，旧节点仍不支持文件恢复。PREPARING时重试先核查旧owner，结果不明的写入按hash跳过，不盲目重发；最多3次attempt。映射与原数据均保留。新Task已开始后禁止重新导入同一Session。回滚边界见学习手册45 §7。

真实合成数据验收（串行，10880端口需空闲，不使用用户旧数据）：

```bash
agentd/.venv/bin/python -m agentd.workspace_migration_demo --nfs-server "$SANDBOXD_NFS_SERVER" \
  --output .cache/workspace-migration.json
```

## 统一命令入口

`bash hack/workspace.sh help`列出固定子命令。脚本不接收任意Shell，服务前台运行，不建立守护器，不清理业务文件或数据卷。

| 阶段 | 命令 | 前置条件与边界 |
|---|---|---|
| 工具/依赖安装 | `bash hack/workspace.sh install` | 复用固定工具安装器与uv frozen全部已声明extras；首次需网络，缓存安装已验证 |
| 环境检查 | `bash hack/workspace.sh check` | 只读；明确kind-sandboxd、NFS项目标签/状态、工具版本和资源 |
| Go构建 | `bash hack/workspace.sh build` | Go1.26.5，隔离可重建缓存，不删除旧镜像 |
| NFS启动 | `bash hack/workspace.sh nfs` | 已构建/核验SANDBOXD_NFS_IMAGE，kind网络已存在；保留named volumes |
| Go前台启动 | `bash hack/workspace.sh go` | 仓库外配置Agent/Operator Token、原生持久SANDBOXD_WORKSPACE_CONTROL_DIR；8080需空闲 |
| Agent前台启动 | `bash hack/workspace.sh agent` | 独立终端，仓库外Agent API/Alert/sandboxd Agent Token与AGENTD_RECOVERY_DIR；8090需空闲 |
| 迁移 | `bash hack/workspace.sh migrate ...` | 参数同上文；apply前停止agentd，控制账本锁防并发 |
| 本地回归 | `bash hack/workspace.sh test` | Python冻结依赖，不请求模型 |
| 独立存储验收 | `bash hack/workspace.sh verify migration` | 真实Go/gVisor/NFS，使用合成数据；10880需空闲 |
| 全套存储夹具 | `bash hack/workspace.sh verify all` | 串行，不与正常8080/8090业务任务共享实验窗口；部分场景临时暂停专属NFS/删除自己的Pod/强杀自己的agentd |
| 三类联合回放 | `bash hack/workspace.sh joint --output .cache/joint.json` | 既有Milvus2.5.10/OpenSearch与缓存模型须先就绪；离线模型缓存，脚本不启动数据库、不重建索引 |

`agent`固定NFS+Replay、Jev关闭；若环境要求live则直接拒绝。它会移除Operator Token环境变量，Python只获得Agent凭据。`go`使用当前固定runtime-v1.5，须先按原安装流程将该镜像载入本项目kind、部署Calico/gVisor/RBAC等基础能力；统一入口不替代这些前置安装。

`verify`每次创建独立`.cache/workspace-eval/run-*`目录，包含逐场景JSON与stdout，失败保留现场与输出，不覆盖旧证据。`all`覆盖存储夹具集合，不等于完整Goal完成：联合服务回放、性能和V2仍分别验收。本批次实际验证缓存install、build、nfs、check、test、entry、joint以及migration/pod分发；verify all未整套重跑，既有逐场景证据保留。

线性旧格式的预览节点属于临时视图，实际节点以COMMITTED映射为准；执行确认使用源Session hash与文件manifest hash。


### 首次镜像准备与前台启动

已存在的runtime-v1.5/NFS镜像应复用，不能覆盖标签后让旧任务静默换镜像。下面是**首次安装、尚无这些镜像**时的构建方式；源、Go1.26.5与基础镜像均固定。已有安装先inspect并复用实际ID，升级另用新版本与显式兼容检查。

```bash
export PATH="$HOME/.local/bin:$PATH"
export GOMODCACHE=/tmp/sandboxd-workspace-go-mod
export GOCACHE=/tmp/sandboxd-workspace-go-build
mkdir -p .cache/workspace-image
if ! docker image inspect sandboxd-workspace:runtime-v1.5 >/dev/null 2>&1; then
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o .cache/workspace-image/sandbox-filetool ./cmd/sandbox-filetool
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o .cache/workspace-image/sandbox-workspace-init ./cmd/sandbox-workspace-init
  docker build -f deploy/workspace/sandbox.Dockerfile -t sandboxd-workspace:runtime-v1.5 .cache/workspace-image
fi
kind load docker-image --name sandboxd sandboxd-workspace:runtime-v1.5
if docker container inspect sandboxd-workspace-nfs-1 >/dev/null 2>&1; then
  export SANDBOXD_NFS_IMAGE="$(docker inspect --format '{{.Image}}' sandboxd-workspace-nfs-1)"
else
  if ! docker image inspect sandboxd-nfs:ganesha-4.3-2 >/dev/null 2>&1; then
    docker build -f deploy/workspace/nfs.Dockerfile -t sandboxd-nfs:ganesha-4.3-2 deploy/workspace
  fi
  export SANDBOXD_NFS_IMAGE="$(docker image inspect --format '{{.Id}}' sandboxd-nfs:ganesha-4.3-2)"
fi
bash hack/workspace.sh nfs
```

Ganesha固定包版本与基础digest见Dockerfile，构建镜像内保存package-versions.txt；构建结果ID以实际inspect为准，不虚构跨时间apt依赖完全相同。当前环境已验证的镜像ID及CRI身份见学习手册45/原始报告；本轮不重新构建已有业务镜像。

仓库外配置好三个不同的Agent API/Alert/sandboxd Agent Token及独立Operator Token。两个终端分别执行：

```bash
export SANDBOXD_WORKSPACE_CONTROL_DIR="$HOME/.local/share/sandboxd/workspace-control"
bash hack/workspace.sh go
```

```bash
export AGENTD_RECOVERY_DIR="$HOME/.local/share/sandboxd/agent-control"
export AGENTD_SANDBOXD_URL=http://127.0.0.1:8080
export AGENTD_LLM_MODE=replay
# 可通过AGENTD_REPLAY_FILE选择固定回放文件，默认沿用原示例。
bash hack/workspace.sh agent
```

要求已按原安装流程建立kind/Calico/gVisor/RBAC；先Go就绪再启动agentd，退出时先agentd再Go。不能把/tmp作为部署控制盘，不把Operator Token传入Python；agent子命令会主动移除它。无外部API Key也可运行Replay。

完整启动/重启与独立性能入口：

```bash
bash hack/workspace.sh entry --output .cache/entry-result.json
bash hack/workspace.sh benchmark --repeats 3 --background-condition serial --output .cache/nfs-benchmark.json
```

两个实验串行；入口实验要求8080/8090空闲，性能实验要求10880及项目工作区空闲。测量口径、缓存、资源/网络边界和原始结果见[46](../../docs/46-工作区恢复性能与流量测量.md)。serial是调用者对项目调度的声明，不保证宿主没有其他后台工作。

## V2 可选联合检查点

### 固定镜像与可信二进制准备

已有kind/Calico/gVisor基础环境时，从仓库根目录执行：

```bash
bash hack/workspace.sh build-storage
```

该入口先检查资源及项目集群、校验固定restic0.18.1下载/二进制，再准备`runtime-v1.5`与`restic:0.18.1-v3`两个镜像。已有tag只inspect复用；缺失时按固定Dockerfile构建。它不覆盖旧镜像、不删除数据卷，保留本次构建输入。当前Go服务与固定Pod渲染器始终从源码构建，分别安装到`.cache/workspace-sandboxd`与`.cache/workspace-backup-pod-owned`，最后加载镜像到项目kind。

复用已有tag不证明镜像来自当前源码；修改helper需明确新版本和兼容检查，不能用这个入口静默升级历史任务。首次无缓存构建可能下载固定基础镜像/restic/Go模块；当前实际验证的是缓存资产校验、已有镜像复用、Go编译及kind加载，原始结果见[安装入口证据](../../docs/evidence/phase9-v2-unified-storage-build.json)。4项命令契约测试使用Fake工具，不能代替首次真实镜像构建。

**此命令只准备镜像和二进制。** 新部署继续执行下面的`init-v2`。未指定部署目录的旧shell restic路径仍使用v2镜像；已有历史共享仓库保留，不自动收编。

### 新专属V2部署：初始化、启动与重启

前置条件：项目kind/Calico/gVisor/RBAC、NFS与专属MinIO已经按本目录及文档47安装；MinIO使用独立`backup-compose.yml`，仓库外私有配置为`~/.local/share/sandboxd/workspace-backup/minio-credentials.json`（`access/secret`），须与该服务实际凭据相同。`init-v2`不启动或重置已有存储，不改RAG MinIO。Go/Python服务所需的四个不同Token仍由仓库外环境提供。

```bash
export SANDBOXD_WORKSPACE_DEPLOYMENT_DIR="$HOME/.local/share/sandboxd/deployments/my-v2"
bash hack/workspace.sh init-v2 --deployment-dir "$SANDBOXD_WORKSPACE_DEPLOYMENT_DIR"
bash hack/workspace.sh status-v2 --deployment-dir "$SANDBOXD_WORKSPACE_DEPLOYMENT_DIR"
```

目录必须是上述原生Linux `deployments`下的一个新私有目录。初始化创建唯一`owned-deploy/<UUID>`仓库前缀、不可变Secret及Owner；状态/密码私有持久化，业务Pod不会得到仓库凭据。重复`init-v2`核对并复用原身份，不生成第二个初始化Pod。提交结果不明时仅查询原Pod；原Pod缺失、失败、UID/spec变化或无法证明停止时拒绝继续，不能删目录后当作恢复。

两个终端均设置相同`SANDBOXD_WORKSPACE_DEPLOYMENT_DIR`，仓库外配置原有Token，然后先Go、后Agent：

```bash
bash hack/workspace.sh go
```

```bash
export AGENTD_SANDBOXD_URL=http://127.0.0.1:8080
bash hack/workspace.sh agent
```

部署目录生成的Go/Agent控制路径、restic模式与逻辑仓库ID必须一致；旧环境若显式配置了不同目录/`off`模式/仓库ID，入口会拒绝，请核对后移除冲突项。不得改Owner或重置原目录来绕过检查。正常重启使用同一环境与同一目录；原Task/attempt/预算语义不变。Agent入口移除Operator Token及存储凭据环境，固定Replay/路由关闭。

`go-args/agent-env`是调用时只读配置视图；仅固定字段，不执行配置内容，不使用`eval/source`。Go启动还核对当前NFS/MinIO地址；地址改变须显式迁移，不自动重指向其他存储。本机目录claim只针对单WSL部署，不能宣传为多主机锁或HA。

实际通过统一入口启动与Agent SIGKILL恢复的演示（只用**已初始化且尚无Task的新目录**）：

```bash
bash hack/workspace.sh entry --deployment-dir "$SANDBOXD_WORKSPACE_DEPLOYMENT_DIR" \
  --output .cache/owned-entry.json
```

这条命令运行固定合成Replay，会强杀自己启动的Agent一次；要求8080/8090及工作区实验窗口空闲。已有业务目录只用于日常启动，不重跑此夹具；旧输出/数据不覆盖。实际结果、独立审计与局限见[文档47 §19](../../docs/47-联合检查点与restic-MinIO学习手册.md#19-owned-v2统一部署与启动恢复)。

NFS继续作为运行工作区，`AGENTD_CHECKPOINT_MODE=restic`启用完整工具组与最终诊断的同步联合保存；默认off。可信Go另需固定维护镜像、专用仓库Secret名称与MinIO私网地址，凭据不能交给业务Pod/模型。安装、两终端启动、独立真实演示及未验收范围见[文档47 §7](../../docs/47-联合检查点与restic-MinIO学习手册.md#7-agent完整边界自动保存与最终诊断接续)。不因有备份就认为历史分支恢复、NFS高可用或自动GC已经实现。

### V2联合提交进程切点

[文档47 §8](../../docs/47-联合检查点与restic-MinIO学习手册.md#8-联合提交切点的真实agent进程实验)提供独立真实SIGKILL夹具：`python -m agentd.workspace_checkpoint_fault_demo --phase <固定切点> --secret-name <专属Secret名称> --minio-ip <已核对私网IP> --output <新报告路径>`。使用项目venv，从仓库根目录执行；每次只操作自己启动的Agent进程，保留全部数据。可选reserved/preparing/before-data-ready/data-ready/before-commit/committed，必须串行；已执行范围按逐个报告核对，不把单个切点当作全部V2完成。默认生产服务没有切点开关。

### V2可信文件恢复组件

已新增固定维护镜像`sandboxd-restic:0.18.1-v2`及`hack/workspace-restore-runtime-smoke`，实际NFS/MinIO新空目标恢复与错误仓库身份写入前拒绝已通过；安装、运行及保留控制目录对账命令见[文档47 §10](../../docs/47-联合检查点与restic-MinIO学习手册.md#10-可信恢复执行器与真实nfsminio验收)。受限restore HTTP与Python历史分支随后已接线，两分支真实服务验收见[文档47 §13](../../docs/47-联合检查点与restic-MinIO学习手册.md#13-taskstoreagent-api与真实历史分支)；off/V1仍不恢复历史文件，完整故障与清理验收待完成。
