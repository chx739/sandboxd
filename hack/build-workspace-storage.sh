#!/usr/bin/env bash
# 首次安装构建固定镜像；已有tag只复用，避免历史Task静默换镜像。
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$repo_root"
export PATH="$HOME/.local/bin:$PATH"
[[ "$(uname -m)" == x86_64 ]] || { echo '仅验证linux/amd64。' >&2; exit 1; }
[[ "$(go version)" == 'go version go1.26.5 '* ]] || { echo '需要Go1.26.5。' >&2; exit 1; }
bash hack/check-resources.sh
bash hack/require-demo-cluster.sh
export GOMODCACHE="${GOMODCACHE:-/tmp/sandboxd-workspace-go-mod}"
export GOCACHE="${GOCACHE:-/tmp/sandboxd-workspace-go-build}"
export CGO_ENABLED=0 GOOS=linux GOARCH=amd64
bash hack/install-restic.sh

mkdir -p .cache
build_dir="$(mktemp -d "$repo_root/.cache/workspace-storage-build-XXXXXXXX")"
# 保留本次构建输入用于复查，不清理旧缓存、镜像或数据卷。
runtime_image=sandboxd-workspace:runtime-v1.5
backup_image=sandboxd-restic:0.18.1-v3
for image in "$runtime_image" "$backup_image"; do
  if docker image inspect "$image" >/dev/null 2>&1; then
    printf '复用已有镜像 %s（不证明与新源码相同；升级须另用新版本）：\n' "$image"
    docker image inspect --format '{{.Id}}' "$image"
    continue
  fi
  case "$image" in
    "$runtime_image")
      go build -o "$build_dir/sandbox-filetool" ./cmd/sandbox-filetool
      go build -o "$build_dir/sandbox-workspace-init" ./cmd/sandbox-workspace-init
      docker build -f deploy/workspace/sandbox.Dockerfile -t "$image" "$build_dir"
      ;;
    "$backup_image")
      install -m 0555 "$HOME/.local/share/sandboxd-tools/restic-0.18.1/restic" "$build_dir/restic"
      printf '%s  %s\n' 01143daba61a1dc8afb0cb3d03ba83e6b118f55b6b146b27473a09efc3df13d6 "$build_dir/restic" | sha256sum -c --status
      go build -o "$build_dir/sandbox-restic" ./cmd/sandbox-restic
      docker build -f deploy/workspace/restic.Dockerfile -t "$image" "$build_dir"
      ;;
  esac
done
# 即使镜像复用，后端和固定Pod渲染器也必须从当前源码构建。
go build -o "$build_dir/workspace-sandboxd" ./cmd/sandboxd
go build -o "$build_dir/workspace-backup-pod-owned" ./hack/workspace-backup-pod
install -m 0755 "$build_dir/workspace-sandboxd" .cache/workspace-sandboxd
install -m 0755 "$build_dir/workspace-backup-pod-owned" .cache/workspace-backup-pod-owned
kind load docker-image --name sandboxd "$runtime_image" "$backup_image"
printf '构建输入保留：%s\n' "$build_dir"
sha256sum "$build_dir/workspace-sandboxd" "$build_dir/workspace-backup-pod-owned"
docker image inspect --format '{{.RepoTags}} {{.Id}}' "$runtime_image" "$backup_image"
