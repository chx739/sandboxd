#!/usr/bin/env bash

set -Eeuo pipefail

# 固定版本和校验值，避免上游 latest 变化后出现“昨天能跑，今天不能跑”。
GO_VERSION="1.26.5"
GO_SHA256="5c2c3b16caefa1d968a94c1daca04a7ca301a496d9b086e17ad77bb81393f053"
KIND_VERSION="0.31.0"
KIND_SHA256="eb244cbafcc157dff60cf68693c14c9a75c4e6e6fedaf9cd71c58117cb93e3fa"
GVISOR_VERSION="20260810.0"
GVISOR_SHA512="3de91138cda15682c11807387f6ecad9e7c8932262018a2813277e1b4efa03efe33b0a948e148c6b1ccfe7345bfab5d5e0d072519505465751273898bae19c62"

machine_arch="$(uname -m)"
if [[ "${machine_arch}" != "x86_64" ]]; then
  echo "仅验证过 x86_64，当前架构为 ${machine_arch}。" >&2
  exit 1
fi

for command_name in curl tar sha256sum sha512sum bzip2 install; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "缺少命令：${command_name}。请先安装它，再重新运行。" >&2
    exit 1
  fi
done

# 默认全部放进用户目录，避免为了 Demo 修改系统级 Go、Docker 或 containerd。
tool_root="${SANDBOXD_TOOL_ROOT:-${HOME}/.local/share/sandboxd-tools}"
bin_dir="${SANDBOXD_BIN_DIR:-${HOME}/.local/bin}"
download_dir="${tool_root}/downloads"
go_dir="${tool_root}/go"
gvisor_dir="${tool_root}/gvisor"

mkdir -p "${bin_dir}" "${download_dir}" "${go_dir}" "${gvisor_dir}"

download_if_missing() {
  local url="$1"
  local output="$2"

  if [[ -f "${output}" ]]; then
    echo "使用已有下载：${output}"
    return
  fi

  echo "下载：${url}"
  curl -fL --retry 3 --output "${output}" "${url}"
}

verify_sha256() {
  local expected="$1"
  local file="$2"
  printf '%s  %s\n' "${expected}" "${file}" | sha256sum --check --status
}

verify_sha512() {
  local expected="$1"
  local file="$2"
  printf '%s  %s\n' "${expected}" "${file}" | sha512sum --check --status
}

go_archive="${download_dir}/go${GO_VERSION}.linux-amd64.tar.gz"
kind_binary="${download_dir}/kind-linux-amd64"
gvisor_archive="${download_dir}/gvisor-${GVISOR_VERSION}.tar.bz2"

download_if_missing \
  "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" \
  "${go_archive}"
download_if_missing \
  "https://github.com/kubernetes-sigs/kind/releases/download/v${KIND_VERSION}/kind-linux-amd64" \
  "${kind_binary}"
download_if_missing \
  "https://storage.googleapis.com/gvisor/releases/release/${GVISOR_VERSION}/x86_64/gvisor.tar.bz2" \
  "${gvisor_archive}"

echo "验证下载文件校验和……"
verify_sha256 "${GO_SHA256}" "${go_archive}"
verify_sha256 "${KIND_SHA256}" "${kind_binary}"
verify_sha512 "${GVISOR_SHA512}" "${gvisor_archive}"

# 固定版本且目录专用，重复解压是安全的；不会清理或覆盖其他用户工具。
tar -xzf "${go_archive}" -C "${go_dir}" --strip-components=1
tar -xjf "${gvisor_archive}" -C "${gvisor_dir}"
install -m 0755 "${kind_binary}" "${bin_dir}/kind"

ln -sfn "${go_dir}/bin/go" "${bin_dir}/go"
ln -sfn "${go_dir}/bin/gofmt" "${bin_dir}/gofmt"
ln -sfn "${gvisor_dir}/runsc" "${bin_dir}/runsc"

echo
"${bin_dir}/go" version
"${bin_dir}/kind" version
"${bin_dir}/runsc" --version
echo
echo "安装完成。若命令仍不可见，请执行：export PATH=\"${bin_dir}:\${PATH}\""
