#!/usr/bin/env bash

set -Eeuo pipefail

expected_cluster="sandboxd"
expected_context="kind-sandboxd"
expected_node="sandboxd-control-plane"

for command_name in kind kubectl; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "缺少命令：${command_name}" >&2
    exit 1
  fi
done

current_context="$(kubectl config current-context 2>/dev/null || true)"
if [[ "${current_context}" != "${expected_context}" ]]; then
  echo "当前 kubectl context 是 ${current_context:-<empty>}，期望 ${expected_context}。" >&2
  echo "为防止误操作其他集群，脚本已停止。" >&2
  exit 1
fi

if ! kind get clusters | grep -qx "${expected_cluster}"; then
  echo "未找到 kind 集群 ${expected_cluster}，请先运行 ./hack/create-cluster.sh。" >&2
  exit 1
fi

nodes="$(kind get nodes --name "${expected_cluster}")"
if [[ "${nodes}" != "${expected_node}" ]]; then
  echo "集群节点不是预期的单节点 ${expected_node}：" >&2
  echo "${nodes}" >&2
  exit 1
fi

kubectl get node "${expected_node}" >/dev/null
echo "集群保护检查通过：context=${expected_context}, node=${expected_node}"
