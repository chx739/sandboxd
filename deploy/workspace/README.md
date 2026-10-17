# NFS V1 部署候选配置

当前状态：M3，Python持久TaskStore/Runner已接入。V1正式13/14项已过，含真实SIGKILL接续、终态不复活及确认丢失；其余V1-14与迁移/性能仍待完成，V2未开始。逐项证据见docs/evidence/phase9-workspace-recovery.md与phase9-v1-acceptance-progress.json。

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

后续统一启动脚本在 M2 完成。不要执行 `docker compose down -v`；Pod 删除与业务数据删除必须分开。

## Python部署默认值与真实验收入口

原有Agent/Alert Token与sandboxd Agent Token继续通过仓库外配置传入；不把Operator Token传给agentd。启动上面的Go服务后：

```bash
export AGENTD_WORKSPACE_BACKEND=nfs
export AGENTD_RECOVERY_DIR="$HOME/.local/share/sandboxd/agent-control"
agentd/.venv/bin/python -m uvicorn agentd.app.main:create_app --factory --host 127.0.0.1 --port 8090
```

环境读取函数默认nfs；control目录须在原生Linux持久盘，不能用/tmp。旧离线流程可显式配置`AGENTD_WORKSPACE_BACKEND=local`，只读旧Session可回退到旧trace目录；旧业务文件未自动搬迁，显式映射/校验迁移仍待交付。

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
