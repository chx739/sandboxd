#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_id="$(tr -d '-' </proc/sys/kernel/random/uuid)"
container_name="sandboxd-linux-target-${run_id}"
image_name="sandboxd-linux-target:phase4"
base_image="gcr.io/k8s-minikube/kicbase:v0.0.50"
base_image_id="sha256:b97074569ae99a5cfca30cc1f8c4793ac0f209946b7448ae54d80c78445ec31d"
runtime_dir="$(mktemp -d /tmp/sandboxd-linux-demo.XXXXXXXX)"
keys_dir="${runtime_dir}/keys"
workspace_dir="${runtime_dir}/workspaces"
target_config="${runtime_dir}/targets.json"
container_created="false"

cleanup() {
  if [[ "${container_created}" == "true" ]]; then
    docker rm --force "${container_name}" >/dev/null 2>&1 || true
  fi
  if [[ "${runtime_dir}" == /tmp/sandboxd-linux-demo.* ]]; then
    rm -rf -- "${runtime_dir}"
  fi
}
trap cleanup EXIT

for command_name in docker ssh ssh-keygen python3; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "缺少命令：${command_name}" >&2
    exit 1
  }
done
if docker container inspect "${container_name}" >/dev/null 2>&1; then
  echo "一次性容器名已存在，拒绝接管：${container_name}" >&2
  exit 1
fi
if [[ "$(docker image inspect "${base_image}" --format '{{.Id}}' 2>/dev/null || true)" \
  != "${base_image_id}" ]]; then
  echo "缺少经过摘要核实的本地基础镜像：${base_image}" >&2
  exit 1
fi

mkdir -m 0700 "${keys_dir}" "${workspace_dir}"
ssh-keygen -q -t ed25519 -N '' -f "${keys_dir}/client_key"
ssh-keygen -q -t ed25519 -N '' -f "${keys_dir}/host_key"
chmod 0600 "${keys_dir}/client_key" "${keys_dir}/host_key"

# authorized_keys 和 sshd_config 同时 ForceCommand，形成远端两层固定白名单。
client_public="$(cut -d ' ' -f 1-2 "${keys_dir}/client_key.pub")"
printf 'restrict,command="/usr/local/bin/sandboxd-forced-command" %s\n' \
  "${client_public}" >"${keys_dir}/authorized_keys"
chmod 0600 "${keys_dir}/authorized_keys"

docker build --pull=false --network=none --tag "${image_name}" \
  "${repo_root}/deploy/linux-target" >/dev/null
docker run --detach --name "${container_name}" \
  --cpus 0.25 --memory 192m --pids-limit 64 \
  --read-only --tmpfs /run:rw,nosuid,noexec,size=16m \
  --tmpfs /tmp:rw,nosuid,noexec,size=16m \
  --publish 127.0.0.1:0:22 \
  --mount "type=bind,src=${keys_dir},dst=/keys,readonly" \
  "${image_name}" >/dev/null
container_created="true"

sleep 1
if [[ "$(docker inspect "${container_name}" --format '{{.State.Running}}')" != "true" ]]; then
  echo "一次性 SSH Target 启动失败" >&2
  docker logs "${container_name}" >&2 || true
  exit 1
fi

port="$(docker port "${container_name}" 22/tcp | sed -n 's/^127\.0\.0\.1://p' | head -n 1)"
if [[ ! "${port}" =~ ^[0-9]+$ ]]; then
  echo "无法解析一次性 SSH 端口" >&2
  exit 1
fi
host_public_type="$(cut -d ' ' -f 1 "${keys_dir}/host_key.pub")"
host_public_data="$(cut -d ' ' -f 2 "${keys_dir}/host_key.pub")"
printf '[127.0.0.1]:%s %s %s\n' \
  "${port}" "${host_public_type}" "${host_public_data}" \
  >"${runtime_dir}/known_hosts"
chmod 0600 "${runtime_dir}/known_hosts"

python3 - "${target_config}" "${port}" "${keys_dir}/client_key" \
  "${runtime_dir}/known_hosts" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(
    json.dumps(
        {
            "targets": [
                {
                    "targetId": "demo-linux",
                    "host": "127.0.0.1",
                    "port": int(sys.argv[2]),
                    "user": "agentdemo",
                    "identityFile": sys.argv[3],
                    "knownHostsFile": sys.argv[4],
                }
            ]
        },
        separators=(",", ":"),
    ),
    encoding="utf-8",
)
path.chmod(0o600)
PY

ready="false"
ssh_demo() {
  local operation="$1"
  ssh -F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=${runtime_dir}/known_hosts" \
    -o GlobalKnownHostsFile=/dev/null -o PasswordAuthentication=no \
    -o KbdInteractiveAuthentication=no -o ClearAllForwardings=yes \
    -o RequestTTY=no -o ConnectTimeout=2 \
    -i "${keys_dir}/client_key" -p "${port}" \
    agentdemo@127.0.0.1 "${operation}"
}
for _ in $(seq 1 30); do
  if ssh_demo host_summary >/dev/null 2>&1; then
    ready="true"
    break
  fi
  sleep 1
done
if [[ "${ready}" != "true" ]]; then
  docker logs "${container_name}" >&2 || true
  exit 1
fi

# 负向路径直接绕开 Python Policy；远端 forced-command 仍必须拒绝任意命令。
set +e
ssh_demo "cat /etc/passwd" >/dev/null 2>&1
forced_command_code="$?"
set -e
if [[ "${forced_command_code}" != "126" ]]; then
  echo "forced-command 未按预期拒绝任意 SSH 命令" >&2
  exit 1
fi

AGENTD_LINUX_TARGETS_FILE="${target_config}" \
AGENTD_WORKSPACE_DIR="${workspace_dir}" \
AGENTD_REPLAY_FILE="${repo_root}/agentd/testdata/phase4-linux-files.replay.json" \
AGENTD_REQUIRE_PHASE4_TOOLS=1 \
  "${repo_root}/hack/run-agent-demo.sh"

echo "Linux target: strict host key + low privilege + forced-command verified"
echo "File tools: task workspace write/read + Trace/Session content redaction verified"
