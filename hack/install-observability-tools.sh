#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cache_dir="$repo_root/.cache"
download_dir="$cache_dir/downloads"
bin_dir="$cache_dir/tools/bin"

prometheus_version="3.14.0"
prometheus_archive="prometheus-$prometheus_version.linux-amd64.tar.gz"
prometheus_sha256="f665c6da19eb7ba399c915d30c7d9793c9b417bf8a749b504bc470678631478d"
prometheus_url="https://github.com/prometheus/prometheus/releases/download/v$prometheus_version/$prometheus_archive"

alertmanager_version="0.34.0"
alertmanager_archive="alertmanager-$alertmanager_version.linux-amd64.tar.gz"
alertmanager_sha256="19c75a11d8c03dc4ade7abdbddfb3a8f28c9e7b000d0849cda0cd71dffd74a03"
alertmanager_url="https://github.com/prometheus/alertmanager/releases/download/v$alertmanager_version/$alertmanager_archive"

extract_dir=""

cleanup() {
  if [[ -n "$extract_dir" && "$extract_dir" == "$cache_dir"/extract.* ]]; then
    rm -rf -- "$extract_dir"
  fi
}
trap cleanup EXIT

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "当前脚本只固定验证 linux-amd64，实际架构为 $(uname -m)。" >&2
  exit 1
fi

for command_name in curl sha256sum tar install mktemp; do
  if ! command -v "$command_name" >/dev/null; then
    echo "缺少命令：$command_name" >&2
    exit 1
  fi
done

"$repo_root/hack/check-resources.sh"
mkdir -p "$download_dir" "$bin_dir"

download_and_check() {
  local url="$1"
  local archive="$2"
  local checksum="$3"
  local target="$download_dir/$archive"

  if [[ ! -f "$target" ]]; then
    echo "下载 $archive"
    curl --fail --location --retry 3 --output "$target.part" "$url"
    mv -- "$target.part" "$target"
  fi

  (
    cd "$download_dir"
    printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check -
  )
}

download_and_check "$prometheus_url" "$prometheus_archive" "$prometheus_sha256"
download_and_check "$alertmanager_url" "$alertmanager_archive" "$alertmanager_sha256"

extract_dir="$(mktemp -d "$cache_dir/extract.XXXXXX")"
tar -xzf "$download_dir/$prometheus_archive" -C "$extract_dir"
tar -xzf "$download_dir/$alertmanager_archive" -C "$extract_dir"

install -m 0755 \
  "$extract_dir/prometheus-$prometheus_version.linux-amd64/prometheus" \
  "$bin_dir/prometheus"
install -m 0755 \
  "$extract_dir/prometheus-$prometheus_version.linux-amd64/promtool" \
  "$bin_dir/promtool"
install -m 0755 \
  "$extract_dir/alertmanager-$alertmanager_version.linux-amd64/alertmanager" \
  "$bin_dir/alertmanager"
install -m 0755 \
  "$extract_dir/alertmanager-$alertmanager_version.linux-amd64/amtool" \
  "$bin_dir/amtool"

"$bin_dir/prometheus" --version | sed -n '1p'
"$bin_dir/alertmanager" --version | sed -n '1p'
echo "Observability tools installed in $bin_dir"
