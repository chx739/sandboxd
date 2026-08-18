#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_dir="${repo_root}/.cache"
evidence_file="${cache_dir}/gvisor-dmesg.txt"
namespace="sandboxd-demo"
pod_name="gvisor-smoke"

cleanup() {
  # 只删除这个脚本创建的精确 Pod，保留集群和 RuntimeClass 供后续模块使用。
  kubectl delete pod "${pod_name}" --namespace "${namespace}" \
    --ignore-not-found --wait=true >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${repo_root}/hack/check-resources.sh"
mkdir -p "${cache_dir}"

kubectl apply -f "${repo_root}/deploy/runtimeclass.yaml"
kubectl apply -f "${repo_root}/deploy/smoke/gvisor-pod.yaml"
kubectl wait --namespace "${namespace}" \
  --for=condition=Ready "pod/${pod_name}" --timeout=180s

runtime_class="$(kubectl get pod "${pod_name}" --namespace "${namespace}" \
  -o jsonpath='{.spec.runtimeClassName}')"
if [[ "${runtime_class}" != "gvisor" ]]; then
  echo "Pod RuntimeClass 不是 gvisor：${runtime_class}" >&2
  exit 1
fi

kubectl exec --namespace "${namespace}" "${pod_name}" -- dmesg >"${evidence_file}"
if ! grep -q "Starting gVisor" "${evidence_file}"; then
  echo "未发现 Starting gVisor，不能证明真实使用了 runsc。" >&2
  sed -n '1,20p' "${evidence_file}" >&2
  exit 1
fi

echo "RuntimeClass: ${runtime_class}"
grep -m1 "Starting gVisor" "${evidence_file}"
echo "完整 dmesg 证据：${evidence_file}"
