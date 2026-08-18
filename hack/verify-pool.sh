#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_dir="${repo_root}/.cache"
binary="${cache_dir}/sandboxd"
server_log="${cache_dir}/sandboxd-pool.log"
metrics_file="${cache_dir}/sandboxd-pool.metrics"
base_url="http://127.0.0.1:8080"
namespace="sandboxd-demo"
demo_token="$(tr -d '-' </proc/sys/kernel/random/uuid)"
server_pid=""
claim_ids=()

managed_selector="sandbox.io/managed-by=sandboxd"
idle_selector="sandbox.io/managed-by=sandboxd,sandbox.io/state=idle"

cleanup() {
  if [[ -n "${server_pid}" ]]; then
    kill "${server_pid}" >/dev/null 2>&1 || true
    wait "${server_pid}" >/dev/null 2>&1 || true
  fi
  # 启动前强制要求 managed Pod=0，因此这里的 selector 只会命中本脚本创建的资源。
  kubectl delete pods --namespace "${namespace}" --selector "${managed_selector}" \
    --ignore-not-found --wait=true >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${repo_root}/hack/check-resources.sh"
mkdir -p "${cache_dir}"

if [[ -n "$(kubectl get pods --namespace "${namespace}" \
  --selector "${managed_selector}" -o name)" ]]; then
  echo "已有 managed Pod，拒绝运行以免干扰现有沙箱。" >&2
  exit 1
fi

go build -o "${binary}" ./cmd/sandboxd
SANDBOXD_TOKEN="${demo_token}" "${binary}" \
  --image curlimages/curl:8.12.1 \
  --pool-size 2 \
  --create-timeout 180s \
  --exec-timeout 10s >"${server_log}" 2>&1 &
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

for _ in $(seq 1 180); do
  idle_count="$(kubectl get pods --namespace "${namespace}" \
    --selector "${idle_selector}" --no-headers 2>/dev/null | wc -l)"
  if [[ "${idle_count}" == "2" ]]; then
    if kubectl wait --namespace "${namespace}" --for=condition=Ready pod \
      --selector "${idle_selector}" --timeout=1s >/dev/null 2>&1; then
      break
    fi
  fi
  sleep 1
done
kubectl wait --namespace "${namespace}" --for=condition=Ready pod \
  --selector "${idle_selector}" --timeout=30s >/dev/null

claim_pids=()
for index in $(seq 1 5); do
  response_file="${cache_dir}/pool-claim-${index}.json"
  code_file="${cache_dir}/pool-claim-${index}.code"
  (
    curl --silent --show-error --output "${response_file}" --write-out '%{http_code}' \
      --request POST --header "Authorization: Bearer ${demo_token}" \
      "${base_url}/api/v1/sandboxes" >"${code_file}"
  ) &
  claim_pids+=("$!")
done
for claim_pid in "${claim_pids[@]}"; do
  wait "${claim_pid}"
done

for index in $(seq 1 5); do
  response_file="${cache_dir}/pool-claim-${index}.json"
  code_file="${cache_dir}/pool-claim-${index}.code"
  [[ "$(tr -d '\r\n' <"${code_file}")" == "201" ]]
  claim_id="$(sed -n 's/.*"id":"\([a-z0-9-]*\)".*/\1/p' "${response_file}")"
  [[ "${claim_id}" =~ ^[a-z0-9-]{1,63}$ ]]
  claim_ids+=("${claim_id}")
done

unique_count="$(printf '%s\n' "${claim_ids[@]}" | sort -u | wc -l)"
[[ "${unique_count}" == "5" ]]
if ! grep -h -q '"source":"pool"' "${cache_dir}"/pool-claim-*.json; then
  echo "五次认领均未命中预热池。" >&2
  exit 1
fi

for claim_id in "${claim_ids[@]}"; do
  delete_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --request DELETE --header "Authorization: Bearer ${demo_token}" \
    "${base_url}/api/v1/sandboxes/${claim_id}")"
  [[ "${delete_code}" == "204" ]]
  kubectl wait --for=delete "pod/sandbox-${claim_id}" \
    --namespace "${namespace}" --timeout=30s >/dev/null
done

for _ in $(seq 1 120); do
  idle_count="$(kubectl get pods --namespace "${namespace}" \
    --selector "${idle_selector}" --no-headers 2>/dev/null | wc -l)"
  if [[ "${idle_count}" == "2" ]] && kubectl wait --namespace "${namespace}" \
    --for=condition=Ready pod --selector "${idle_selector}" \
    --timeout=1s >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
final_idle_count="$(kubectl get pods --namespace "${namespace}" \
  --selector "${idle_selector}" --no-headers | wc -l)"
[[ "${final_idle_count}" == "2" ]]

# Gauge 来自控制器最近一次 Reconcile，先等待状态收敛，再读取指标。
curl --fail --silent "${base_url}/metrics" >"${metrics_file}"
grep -q '^sandbox_runtime_info{runtime="gvisor"} 1$' "${metrics_file}"
grep -q '^sandbox_pool_size{state="idle"} 2$' "${metrics_file}"
grep -q '^sandbox_pool_size{state="busy"} 0$' "${metrics_file}"
grep -Eq '^sandbox_acquire_seconds_count\{source="pool"\} [1-9][0-9]*$' "${metrics_file}"
grep -Eq '^sandbox_acquire_seconds_count\{source="direct"\} [1-9][0-9]*$' "${metrics_file}"
grep -q '^sandbox_claim_conflicts_total ' "${metrics_file}"

pool_sources="$(grep -h -c '"source":"pool"' "${cache_dir}"/pool-claim-*.json | awk '{sum += $1} END {print sum}')"
direct_sources=$((5 - pool_sources))
claim_conflicts="$(awk '$1 == "sandbox_claim_conflicts_total" {print $2}' "${metrics_file}")"
echo "Pool target: 2 Ready idle"
echo "Concurrent Claim: 5 requests, 5 unique IDs"
echo "Claim source: pool=${pool_sources}, direct=${direct_sources}"
echo "Release + reconcile: idle restored to 2"
echo "Metrics: runtime=gvisor, idle=2, busy=0, claim_conflicts=${claim_conflicts}"
