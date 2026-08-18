#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
namespace="sandboxd-demo"
pod_name="security-smoke"
identity="system:serviceaccount:${namespace}:sandbox-reader"

cleanup() {
  # 只删除本脚本创建的精确临时 Pod，安全策略继续保留给后续模块。
  kubectl delete pod "${pod_name}" --namespace "${namespace}" \
    --ignore-not-found --wait=true >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${repo_root}/hack/apply-security.sh"

expect_auth() {
  local expected="$1"
  shift
  local actual
  actual="$(kubectl auth can-i "$@" --as "${identity}" || true)"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "RBAC 验证失败：kubectl auth can-i $* = ${actual}，期望 ${expected}" >&2
    exit 1
  fi
  echo "RBAC: $* -> ${actual}"
}

expect_auth yes get pods --all-namespaces
expect_auth no create pods --namespace "${namespace}"
expect_auth no get secrets --all-namespaces
expect_auth no create pods --subresource=exec --namespace "${namespace}"

kubectl apply -f "${repo_root}/deploy/runtimeclass.yaml"
kubectl apply -f "${repo_root}/deploy/smoke/policy-pod.yaml"
kubectl wait --namespace "${namespace}" \
  --for=condition=Ready "pod/${pod_name}" --timeout=180s

service_account_dir="/var/run/secrets/kubernetes.io/serviceaccount"
http_code="$(kubectl exec --namespace "${namespace}" "${pod_name}" -- sh -c \
  "curl --silent --show-error --cacert ${service_account_dir}/ca.crt \
    --header \"Authorization: Bearer \$(cat ${service_account_dir}/token)\" \
    --output /dev/null --write-out '%{http_code}' \
    https://kubernetes.default.svc/api/v1/namespaces/${namespace}/pods")"
if [[ "${http_code}" != "200" ]]; then
  echo "沙箱内只读 API 请求返回 ${http_code}，期望 200。" >&2
  exit 1
fi
echo "NetworkPolicy + token + RBAC: 集群内读取 Pod -> HTTP ${http_code}"

if kubectl exec --namespace "${namespace}" "${pod_name}" -- \
  curl --fail --silent --show-error --connect-timeout 3 --max-time 5 \
  --output /dev/null https://example.com; then
  echo "普通公网请求意外成功，default-deny egress 未生效。" >&2
  exit 1
fi
echo "NetworkPolicy: https://example.com -> 已按预期拒绝"
