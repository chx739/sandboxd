#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_dir="${repo_root}/.cache"
rendered_policy="${cache_dir}/networkpolicy.yaml"

"${repo_root}/hack/check-resources.sh"
mkdir -p "${cache_dir}"

# kind 的 API Server endpoint 可能在重建集群后变化，所以不把本机 IP 写死进 Git。
# Kubernetes 1.33 起 Endpoints 已弃用，这里直接读取 EndpointSlice。
apiserver_ip="$(kubectl get endpointslice --namespace default \
  --selector=kubernetes.io/service-name=kubernetes \
  -o jsonpath='{.items[0].endpoints[0].addresses[0]}')"
apiserver_port="$(kubectl get endpointslice --namespace default \
  --selector=kubernetes.io/service-name=kubernetes \
  -o jsonpath='{.items[0].ports[0].port}')"

if [[ ! "${apiserver_ip}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "无法识别 API Server IPv4 endpoint：${apiserver_ip}" >&2
  exit 1
fi
if [[ ! "${apiserver_port}" =~ ^[0-9]+$ ]]; then
  echo "无法识别 API Server 端口：${apiserver_port}" >&2
  exit 1
fi

sed \
  -e "s|@APISERVER_IP@|${apiserver_ip}|g" \
  -e "s|@APISERVER_PORT@|${apiserver_port}|g" \
  "${repo_root}/deploy/networkpolicy.yaml.tmpl" >"${rendered_policy}"

kubectl apply -f "${repo_root}/deploy/namespace.yaml"
kubectl apply -f "${repo_root}/deploy/rbac.yaml"
kubectl apply -f "${rendered_policy}"

echo "安全清单已应用，API Server endpoint：${apiserver_ip}:${apiserver_port}"
