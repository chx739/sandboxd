#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_dir="${repo_root}/.cache"
binary="${cache_dir}/sandboxd"
server_log="${cache_dir}/sandboxd-approval.log"
metrics_file="${cache_dir}/sandboxd-approval.metrics"
base_url="http://127.0.0.1:8080"
target_namespace="sandboxd-target"
target_name="approval-demo"
agent_token="$(tr -d '-' </proc/sys/kernel/random/uuid)"
operator_token="$(tr -d '-' </proc/sys/kernel/random/uuid)"
server_pid=""
created_namespace="false"

cleanup() {
  if [[ -n "${server_pid}" ]]; then
    kill "${server_pid}" >/dev/null 2>&1 || true
    wait "${server_pid}" >/dev/null 2>&1 || true
  fi
  if [[ "${created_namespace}" == "true" ]]; then
    kubectl delete namespace "${target_namespace}" --wait=true \
      --ignore-not-found >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

"${repo_root}/hack/check-resources.sh"
mkdir -p "${cache_dir}"

# 不接管同名 namespace，避免 cleanup 删除用户已有资源。
if kubectl get namespace "${target_namespace}" >/dev/null 2>&1; then
  echo "namespace ${target_namespace} 已存在，拒绝运行以免覆盖或误删。" >&2
  exit 1
fi
kubectl apply -f "${repo_root}/deploy/smoke/approval-target.yaml" >/dev/null
created_namespace="true"
[[ "$(kubectl get deployment "${target_name}" --namespace "${target_namespace}" \
  -o jsonpath='{.spec.replicas}')" == "0" ]]

go build -o "${binary}" ./cmd/sandboxd
SANDBOXD_TOKEN="${agent_token}" SANDBOXD_OPERATOR_TOKEN="${operator_token}" "${binary}" \
  --pool-size 0 >"${server_log}" 2>&1 &
server_pid="$!"

for _ in $(seq 1 30); do
  if curl --fail --silent "${base_url}/healthz" >/dev/null; then
    break
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    sed -n '1,100p' "${server_log}" >&2
    exit 1
  fi
  sleep 1
done
curl --fail --silent "${base_url}/healthz" >/dev/null

namespace_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --request POST --header "Authorization: Bearer ${agent_token}" \
  --header 'Content-Type: application/json' \
  --data '{"namespace":"kube-system","name":"approval-demo","replicas":1}' \
  "${base_url}/api/v1/plans")"
[[ "${namespace_code}" == "400" ]]

replicas_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --request POST --header "Authorization: Bearer ${agent_token}" \
  --header 'Content-Type: application/json' \
  --data '{"namespace":"sandboxd-target","name":"approval-demo","replicas":11}' \
  "${base_url}/api/v1/plans")"
[[ "${replicas_code}" == "400" ]]

approve_plan_file="${cache_dir}/approval-plan.json"
approve_plan_code="$(curl --silent --show-error --output "${approve_plan_file}" --write-out '%{http_code}' \
  --request POST --header "Authorization: Bearer ${agent_token}" \
  --header 'Content-Type: application/json' \
  --data '{"namespace":"sandboxd-target","name":"approval-demo","replicas":1}' \
  "${base_url}/api/v1/plans")"
[[ "${approve_plan_code}" == "201" ]]
grep -q '"status":"pending"' "${approve_plan_file}"
grep -q '"dryRunValidated":true' "${approve_plan_file}"
approve_plan_id="$(sed -n 's/.*"id":"\([a-f0-9]*\)".*/\1/p' "${approve_plan_file}")"
[[ "${approve_plan_id}" =~ ^[a-f0-9]{16}$ ]]

# DryRun 只校验，不应在 Operator 决策前修改真实副本数。
[[ "$(kubectl get deployment "${target_name}" --namespace "${target_namespace}" \
  -o jsonpath='{.spec.replicas}')" == "0" ]]

agent_approve_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --request POST --header "Authorization: Bearer ${agent_token}" \
  "${base_url}/api/v1/plans/${approve_plan_id}/approve")"
[[ "${agent_approve_code}" == "401" ]]

operator_list_file="${cache_dir}/approval-list.json"
curl --fail --silent --header "Authorization: Bearer ${operator_token}" \
  "${base_url}/api/v1/plans" >"${operator_list_file}"
grep -q "${approve_plan_id}" "${operator_list_file}"

approve_file="${cache_dir}/approval-approved.json"
curl --fail --silent --show-error --output "${approve_file}" \
  --request POST --header "Authorization: Bearer ${operator_token}" \
  "${base_url}/api/v1/plans/${approve_plan_id}/approve"
grep -q '"status":"approved"' "${approve_file}"
[[ "$(kubectl get deployment "${target_name}" --namespace "${target_namespace}" \
  -o jsonpath='{.spec.replicas}')" == "1" ]]
kubectl rollout status deployment/"${target_name}" --namespace "${target_namespace}" \
  --timeout=120s >/dev/null

stale_plan_file="${cache_dir}/approval-stale-plan.json"
curl --fail --silent --show-error --output "${stale_plan_file}" \
  --request POST --header "Authorization: Bearer ${agent_token}" \
  --header 'Content-Type: application/json' \
  --data '{"namespace":"sandboxd-target","name":"approval-demo","replicas":0}' \
  "${base_url}/api/v1/plans"
stale_plan_id="$(sed -n 's/.*"id":"\([a-f0-9]*\)".*/\1/p' "${stale_plan_file}")"
[[ "${stale_plan_id}" =~ ^[a-f0-9]{16}$ ]]

# 模拟审批窗口中被其他控制器/管理员改动，即使 replicas 未变也必须重新审核。
kubectl annotate deployment "${target_name}" --namespace "${target_namespace}" \
  sandboxd.io/touch="$(date +%s%N)" >/dev/null
stale_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --request POST --header "Authorization: Bearer ${operator_token}" \
  "${base_url}/api/v1/plans/${stale_plan_id}/approve")"
[[ "${stale_code}" == "409" ]]
[[ "$(kubectl get deployment "${target_name}" --namespace "${target_namespace}" \
  -o jsonpath='{.spec.replicas}')" == "1" ]]

reject_plan_file="${cache_dir}/approval-reject-plan.json"
curl --fail --silent --show-error --output "${reject_plan_file}" \
  --request POST --header "Authorization: Bearer ${agent_token}" \
  --header 'Content-Type: application/json' \
  --data '{"namespace":"sandboxd-target","name":"approval-demo","replicas":0}' \
  "${base_url}/api/v1/plans"
reject_plan_id="$(sed -n 's/.*"id":"\([a-f0-9]*\)".*/\1/p' "${reject_plan_file}")"
[[ "${reject_plan_id}" =~ ^[a-f0-9]{16}$ ]]

reject_file="${cache_dir}/approval-rejected.json"
curl --fail --silent --show-error --output "${reject_file}" \
  --request POST --header "Authorization: Bearer ${operator_token}" \
  "${base_url}/api/v1/plans/${reject_plan_id}/reject"
grep -q '"status":"rejected"' "${reject_file}"
reapprove_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --request POST --header "Authorization: Bearer ${operator_token}" \
  "${base_url}/api/v1/plans/${reject_plan_id}/approve")"
[[ "${reapprove_code}" == "409" ]]
[[ "$(kubectl get deployment "${target_name}" --namespace "${target_namespace}" \
  -o jsonpath='{.spec.replicas}')" == "1" ]]

curl --fail --silent "${base_url}/metrics" >"${metrics_file}"
grep -q '^sandbox_plan_denied_total{reason="namespace"} 1$' "${metrics_file}"
grep -q '^sandbox_plan_denied_total{reason="replicas"} 1$' "${metrics_file}"
grep -q '^sandbox_plan_denied_total{reason="changed"} 1$' "${metrics_file}"
grep -q '^sandbox_plan_denied_total{reason="state"} 1$' "${metrics_file}"

curl --fail --silent --header "Authorization: Bearer ${agent_token}" \
  "${base_url}/api/v1/plans" >"${operator_list_file}"
grep -q '"status":"approved"' "${operator_list_file}"
grep -q '"status":"stale"' "${operator_list_file}"
grep -q '"status":"rejected"' "${operator_list_file}"

echo "DryRun: replicas remained 0 before approval"
echo "Role split: Agent approve -> 401, Operator list/approve -> 200"
echo "Approve: Deployment replicas 0 -> 1, gVisor Pod became Available"
echo "TOCTOU: resourceVersion changed -> 409, Plan stale, replicas stayed 1"
echo "Reject: Plan rejected, repeated approve -> 409, replicas stayed 1"
echo "Policy metrics: namespace=1, replicas=1, changed=1, state=1"
