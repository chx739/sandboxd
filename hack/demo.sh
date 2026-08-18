#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${SANDBOXD_BIN_DIR:-${HOME}/.local/bin}:${PATH}"

"${repo_root}/hack/require-demo-cluster.sh"
"${repo_root}/hack/check-resources.sh"

# 先应用安全边界，再运行 workload；各阶段都有自己的精确 cleanup。
steps=(
  verify-security.sh
  verify-gvisor.sh
  verify-manager.sh
  verify-pool.sh
  verify-approval.sh
)

for step in "${steps[@]}"; do
  echo
  echo "运行 ${step}"
  "${repo_root}/hack/${step}"
done

if [[ -n "$(kubectl get pods --namespace sandboxd-demo \
  --selector=sandbox.io/managed-by=sandboxd -o name)" ]]; then
  echo "最终审计发现残留 managed Pod。" >&2
  exit 1
fi
if kubectl get namespace sandboxd-target >/dev/null 2>&1; then
  echo "最终审计发现残留 sandboxd-target namespace。" >&2
  exit 1
fi

echo
echo "sandboxd 最小 Demo 全部通过；临时 workload 已清理，kind 集群保留。"
