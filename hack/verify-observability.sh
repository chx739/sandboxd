#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
bin_dir="$repo_root/.cache/tools/bin"
runtime_dir="$repo_root/.cache/observability"
config_dir="$runtime_dir/config"
prometheus_data="$runtime_dir/prometheus-data"
alertmanager_data="$runtime_dir/alertmanager-data"
prometheus_log="$runtime_dir/prometheus.log"
alertmanager_log="$runtime_dir/alertmanager.log"

prometheus_pid=""
alertmanager_pid=""

cleanup() {
  if [[ -n "$prometheus_pid" ]]; then
    kill "$prometheus_pid" >/dev/null 2>&1 || true
    wait "$prometheus_pid" >/dev/null 2>&1 || true
  fi
  if [[ -n "$alertmanager_pid" ]]; then
    kill "$alertmanager_pid" >/dev/null 2>&1 || true
    wait "$alertmanager_pid" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for binary in prometheus promtool alertmanager amtool; do
  if [[ ! -x "$bin_dir/$binary" ]]; then
    echo "缺少 $bin_dir/$binary，请先运行 hack/install-observability-tools.sh" >&2
    exit 1
  fi
done

for port in 9090 9093; do
  if ss -H -ltn "sport = :$port" | grep -q .; then
    echo "端口 $port 已被占用，拒绝接管未知进程。" >&2
    exit 1
  fi
done

"$repo_root/hack/check-resources.sh"
mkdir -p "$config_dir" "$prometheus_data" "$alertmanager_data"
cp -- "$repo_root/deploy/observability/prometheus.yml" "$config_dir/prometheus.yml"
cp -- "$repo_root/deploy/observability/alert-rules.yml" "$config_dir/alert-rules.yml"

alert_token="$(tr -d '-' </proc/sys/kernel/random/uuid)"
sed "s/@AGENTD_ALERT_TOKEN@/$alert_token/g" \
  "$repo_root/deploy/observability/alertmanager.yml.template" \
  >"$config_dir/alertmanager.yml"
chmod 0600 "$config_dir/alertmanager.yml"

(
  cd "$config_dir"
  "$bin_dir/promtool" check config prometheus.yml
)
"$bin_dir/amtool" check-config "$config_dir/alertmanager.yml"

(
  cd "$config_dir"
  exec "$bin_dir/alertmanager" \
    --config.file=alertmanager.yml \
    --storage.path="$alertmanager_data" \
    --web.listen-address=127.0.0.1:9093 \
    --log.level=warn
) >"$alertmanager_log" 2>&1 &
alertmanager_pid="$!"

(
  cd "$config_dir"
  exec "$bin_dir/prometheus" \
    --config.file=prometheus.yml \
    --storage.tsdb.path="$prometheus_data" \
    --storage.tsdb.retention.time=2h \
    --web.listen-address=127.0.0.1:9090 \
    --log.level=warn
) >"$prometheus_log" 2>&1 &
prometheus_pid="$!"

for _ in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:9090/-/ready >/dev/null \
    && curl --fail --silent http://127.0.0.1:9093/-/ready >/dev/null; then
    break
  fi
  if ! kill -0 "$prometheus_pid" 2>/dev/null \
    || ! kill -0 "$alertmanager_pid" 2>/dev/null; then
    sed -n '1,120p' "$prometheus_log" >&2 || true
    sed -n '1,120p' "$alertmanager_log" >&2 || true
    exit 1
  fi
  sleep 1
done

curl --fail --silent http://127.0.0.1:9090/-/ready >/dev/null
curl --fail --silent http://127.0.0.1:9093/-/ready >/dev/null

alerts_file="$runtime_dir/alerts.json"
for _ in $(seq 1 30); do
  curl --fail --silent http://127.0.0.1:9090/api/v1/alerts >"$alerts_file"
  if grep -q '"alertname":"SandboxAgentDemoCrashLoop"' "$alerts_file" \
    && grep -q '"state":"firing"' "$alerts_file"; then
    echo "Prometheus alert SandboxAgentDemoCrashLoop is firing"
    echo "Alertmanager is ready on 127.0.0.1:9093"
    exit 0
  fi
  sleep 1
done

echo "等待确定性告警 firing 超时。" >&2
sed -n '1,160p' "$prometheus_log" >&2 || true
exit 1
