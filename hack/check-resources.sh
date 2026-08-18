#!/usr/bin/env bash

set -Eeuo pipefail

# 这是保护 WSL 的只读前置检查：不创建容器，也不修改任何系统配置。
minimum_available_kib=$((2 * 1024 * 1024))
available_kib="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
swap_total_kib="$(awk '/SwapTotal:/ {print $2}' /proc/meminfo)"
swap_free_kib="$(awk '/SwapFree:/ {print $2}' /proc/meminfo)"
swap_used_kib=$((swap_total_kib - swap_free_kib))

echo "CPU: $(nproc)"
echo "可用内存: $((available_kib / 1024)) MiB"
echo "已用 swap: $((swap_used_kib / 1024)) MiB"
df -h / /mnt/c 2>/dev/null || df -h /

if ((available_kib < minimum_available_kib)); then
  echo "可用内存低于 2 GiB，停止重操作。" >&2
  exit 1
fi

if command -v docker >/dev/null 2>&1; then
  running_containers="$(docker ps --quiet | wc -l)"
  echo "运行中的 Docker 容器: ${running_containers}"
else
  echo "未找到 Docker。" >&2
  exit 1
fi

echo "资源检查通过，可继续执行单节点、低并发 Demo。"
