#!/usr/bin/env bash

set -Eeuo pipefail

CALICO_VERSION="3.32.0"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_dir="${repo_root}/.cache"
manifest="${cache_dir}/calico-v${CALICO_VERSION}.yaml"
manifest_url="https://raw.githubusercontent.com/projectcalico/calico/v${CALICO_VERSION}/manifests/calico.yaml"

"${repo_root}/hack/check-resources.sh"
mkdir -p "${cache_dir}"

if [[ ! -f "${manifest}" ]]; then
  echo "下载 Calico v${CALICO_VERSION} 官方清单……"
  curl -fL --retry 3 --output "${manifest}" "${manifest_url}"
fi

kubectl apply -f "${manifest}"

echo "等待 Calico 和节点 Ready；首次拉取镜像可能需要几分钟……"
kubectl wait --namespace kube-system \
  --for=condition=Ready pod \
  --selector=k8s-app=calico-node \
  --timeout=300s
kubectl wait --namespace kube-system \
  --for=condition=Ready pod \
  --selector=k8s-app=calico-kube-controllers \
  --timeout=300s
kubectl wait --for=condition=Ready node --all --timeout=120s

kubectl get nodes -o wide
kubectl get pods -n kube-system -l k8s-app=calico-node
