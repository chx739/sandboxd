#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_dir="${repo_root}/.cache"
binary="${cache_dir}/sandboxd"
server_log="${cache_dir}/sandboxd.log"
metrics_file="${cache_dir}/sandboxd-manager.metrics"
base_url="http://127.0.0.1:8080"
demo_token="$(tr -d '-' </proc/sys/kernel/random/uuid)"
server_pid=""
created_ids=()

cleanup() {
  for sandbox_id in "${created_ids[@]}"; do
    if [[ "${sandbox_id}" =~ ^[a-z0-9-]{1,63}$ ]]; then
      kubectl delete pod "sandbox-${sandbox_id}" --namespace sandboxd-demo \
        --ignore-not-found --wait=false >/dev/null 2>&1 || true
    fi
  done
  if [[ -n "${server_pid}" ]]; then
    kill "${server_pid}" >/dev/null 2>&1 || true
    wait "${server_pid}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

"${repo_root}/hack/check-resources.sh"
mkdir -p "${cache_dir}"

go build -o "${binary}" ./cmd/sandboxd
SANDBOXD_TOKEN="${demo_token}" "${binary}" \
  --image curlimages/curl:8.12.1 \
  --pool-size 0 \
  --create-timeout 180s \
  --exec-timeout 3s >"${server_log}" 2>&1 &
server_pid="$!"

for _ in $(seq 1 30); do
  if curl --fail --silent "${base_url}/healthz" >/dev/null; then
    break
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    echo "sandboxd 提前退出：" >&2
    sed -n '1,80p' "${server_log}" >&2
    exit 1
  fi
  sleep 1
done
curl --fail --silent "${base_url}/healthz" >/dev/null

unauthorized_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "${base_url}/api/v1/sandboxes")"
[[ "${unauthorized_code}" == "401" ]]

create_file="${cache_dir}/manager-create.json"
create_code="$(curl --silent --show-error --output "${create_file}" --write-out '%{http_code}' \
  --request POST --header "Authorization: Bearer ${demo_token}" \
  "${base_url}/api/v1/sandboxes")"
[[ "${create_code}" == "201" ]]
sandbox_id="$(sed -n 's/.*"id":"\([a-z0-9-]*\)".*/\1/p' "${create_file}")"
[[ "${sandbox_id}" =~ ^[a-z0-9-]{1,63}$ ]]
created_ids+=("${sandbox_id}")

exec_ok_file="${cache_dir}/manager-exec-ok.json"
curl --fail --silent --show-error --output "${exec_ok_file}" \
  --request POST \
  --header "Authorization: Bearer ${demo_token}" \
  --header 'Content-Type: application/json' \
  --data '{"cmd":["sh","-c","echo stdout-ok; echo stderr-ok >&2"]}' \
  "${base_url}/api/v1/sandboxes/${sandbox_id}/exec"
grep -q '"exitCode":0' "${exec_ok_file}"
grep -q 'stdout-ok' "${exec_ok_file}"
grep -q 'stderr-ok' "${exec_ok_file}"

exec_exit_file="${cache_dir}/manager-exec-exit.json"
curl --fail --silent --show-error --output "${exec_exit_file}" \
  --request POST \
  --header "Authorization: Bearer ${demo_token}" \
  --header 'Content-Type: application/json' \
  --data '{"cmd":["sh","-c","echo before-exit; exit 7"]}' \
  "${base_url}/api/v1/sandboxes/${sandbox_id}/exec"
grep -q '"exitCode":7' "${exec_exit_file}"

exec_timeout_file="${cache_dir}/manager-exec-timeout.json"
timeout_code="$(curl --silent --show-error --output "${exec_timeout_file}" --write-out '%{http_code}' \
  --request POST \
  --header "Authorization: Bearer ${demo_token}" \
  --header 'Content-Type: application/json' \
  --data '{"cmd":["sh","-c","sleep 10"]}' \
  "${base_url}/api/v1/sandboxes/${sandbox_id}/exec")"
[[ "${timeout_code}" == "504" ]]
kubectl wait --for=delete "pod/sandbox-${sandbox_id}" \
  --namespace sandboxd-demo --timeout=30s

delete_create_file="${cache_dir}/manager-create-delete.json"
curl --fail --silent --show-error --output "${delete_create_file}" \
  --request POST --header "Authorization: Bearer ${demo_token}" \
  "${base_url}/api/v1/sandboxes"
delete_id="$(sed -n 's/.*"id":"\([a-z0-9-]*\)".*/\1/p' "${delete_create_file}")"
[[ "${delete_id}" =~ ^[a-z0-9-]{1,63}$ ]]
created_ids+=("${delete_id}")

delete_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --request DELETE --header "Authorization: Bearer ${demo_token}" \
  "${base_url}/api/v1/sandboxes/${delete_id}")"
[[ "${delete_code}" == "204" ]]
kubectl wait --for=delete "pod/sandbox-${delete_id}" \
  --namespace sandboxd-demo --timeout=30s

if [[ -n "$(kubectl get pods --namespace sandboxd-demo \
  --selector=sandbox.io/managed-by=sandboxd -o name)" ]]; then
  echo "验收结束后仍有 managed Pod。" >&2
  exit 1
fi

curl --fail --silent "${base_url}/metrics" >"${metrics_file}"
grep -q '^sandbox_runtime_info{runtime="gvisor"} 1$' "${metrics_file}"
grep -q '^sandbox_exec_seconds_count 3$' "${metrics_file}"
grep -q '^sandbox_exec_timeouts_total 1$' "${metrics_file}"
grep -q '^sandbox_acquire_seconds_count{source="direct"} 2$' "${metrics_file}"

echo "HTTP auth: unauthorized -> 401"
echo "Create + informer Ready: -> 201"
echo "Exec success: exitCode=0，stdout/stderr 已分离"
echo "Exec failure: exitCode=7，未错误 fallback"
echo "Exec timeout: -> 504，Pod 已删除"
echo "Delete: -> 204，managed Pod=0"
echo "Metrics: direct acquire=2, exec=3, timeout=1"
