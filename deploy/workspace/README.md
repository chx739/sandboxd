# NFS V1 部署候选配置

当前状态：M0/M1，真实 NFSv4.1 + 非 root gVisor 文件 helper 组件探测已通过；Agent RPC 与自动恢复尚未接通。原始证据：docs/evidence/phase9-nfs-component.json。

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
docker build -f deploy/workspace/sandbox.Dockerfile -t sandboxd-workspace:probe-v1 .cache/workspace-image
kind load docker-image sandboxd-workspace:probe-v1 --name sandboxd
```

M0 首次探测目录由管理员固定命令创建；这一步还不等于默认 Go 可信目录初始化已实现：

```bash
docker exec --user 65532:65532 sandboxd-workspace-nfs-1 mkdir -m 0700 /export/workspaces/workspace-00000000000000000000000000000001
export SANDBOXD_SMOKE_WORKSPACE_ID=workspace-00000000000000000000000000000001
export SANDBOXD_NFS_SERVER="$(docker inspect sandboxd-workspace-nfs-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
export SANDBOXD_WORKSPACE_IMAGE=sandboxd-workspace:probe-v1
go run ./hack/workspace-fixture.go > .cache/phase9-workspace-fixture.json
kubectl --context kind-sandboxd apply -f .cache/phase9-workspace-fixture.json
kubectl --context kind-sandboxd -n sandboxd-demo wait --for=condition=Ready pod/sandbox-workspace-probe --timeout=60s
python3 hack/verify-workspace-nfs.py --output .cache/phase9-nfs-component-rerun.json
```

已存在的合成目录须先核对归属，不删除后重建；mkdir 因存在而报错时不要把所有错误都忽略。探测工具仅访问固定合成工作区，直接管理员 exec 用于组件验收，不能宣传为 Agent 文件 RPC 验收。

依据：[Ganesha VFS](https://github.com/nfs-ganesha/nfs-ganesha/wiki/VFS)、[SIG Storage 服务端部署说明](https://github.com/kubernetes-sigs/nfs-ganesha-server-and-external-provisioner/blob/master/docs/deployment.md)。
