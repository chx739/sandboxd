#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cluster_name="sandboxd"
tool_root="${SANDBOXD_TOOL_ROOT:-${HOME}/.local/share/sandboxd-tools}"
gvisor_dir="${tool_root}/gvisor"
cache_dir="${repo_root}/.cache"
rendered_config="${cache_dir}/kind-config.yaml"

export PATH="${SANDBOXD_BIN_DIR:-${HOME}/.local/bin}:${PATH}"

"${repo_root}/hack/check-resources.sh"

for command_name in docker kind kubectl; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "缺少命令：${command_name}" >&2
    exit 1
  fi
done

for required_file in \
  "${gvisor_dir}/runsc" \
  "${gvisor_dir}/containerd-shim-runsc-v1" \
  "${gvisor_dir}/gvisor-bin/gvisor_sentry"; do
  if [[ ! -x "${required_file}" ]]; then
    echo "gVisor 文件不存在或不可执行：${required_file}" >&2
    echo "请先运行 ./hack/install-tools.sh" >&2
    exit 1
  fi
done

if kind get clusters | grep -qx "${cluster_name}"; then
  echo "kind 集群 ${cluster_name} 已存在，不重复创建。"
  exit 0
fi

mkdir -p "${cache_dir}"
# kind 配置不展开环境变量，所以只在本机缓存中写入实际用户路径。
sed "s|@GVISOR_DIR@|${gvisor_dir}|g" \
  "${repo_root}/deploy/kind/config.yaml.tmpl" >"${rendered_config}"

echo "创建单节点 kind 集群 ${cluster_name}……"
kind create cluster --config "${rendered_config}"

echo "集群已创建。由于默认 CNI 被关闭，下一步必须运行 ./hack/install-calico.sh。"
