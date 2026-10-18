#!/usr/bin/env bash
# Phase9统一入口；凭据只从环境传入，服务前台运行，不自行守护或清理数据。
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$repo_root"
export PATH="$HOME/.local/bin:$PATH"
python_bin="$repo_root/agentd/.venv/bin/python"
binary="$repo_root/.cache/workspace-sandboxd"
action="${1:-help}"
if (($#)); then shift; fi

check_cluster() {
  bash hack/check-resources.sh
  bash hack/require-demo-cluster.sh
}

nfs_address() {
  local project running paused
  project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' sandboxd-workspace-nfs-1)"
  running="$(docker inspect --format '{{.State.Running}}' sandboxd-workspace-nfs-1)"
  paused="$(docker inspect --format '{{.State.Paused}}' sandboxd-workspace-nfs-1)"
  [[ "$project" == sandboxd-workspace && "$running" == true && "$paused" == false ]] || {
    echo '项目NFS未正常运行；停止操作。' >&2; return 1;
  }
  docker inspect --format '{{(index .NetworkSettings.Networks "kind").IPAddress}}' sandboxd-workspace-nfs-1
}

case "$action" in
  help)
    cat <<'EOF'
用法：bash hack/workspace.sh ACTION [参数]
  install       固定工具与uv frozen全部已声明extras（首次需要网络）
  check         只读资源/集群/NFS/版本检查
  build         编译可信Go服务（要求Go1.26.5）
  nfs           启动已配置的专属NFS；要求已核实SANDBOXD_NFS_IMAGE
  go            前台启动Go服务；要求Agent/Operator Token及原生持久控制目录
  agent         前台启动NFS+Replay agentd；路由关闭，不调用外部模型
  test          本地Python全量回归
  migrate ARGS  旧工作区预览/显式导入，参数见workspace_migrate --help
  entry ARGS    通过本入口启动Go/agentd并验收重启接续；要求--output
  benchmark ARGS 固定NFS文本负载；要求--output，可设--repeats 1..5
  verify CASE   真实存储验收，独立输出目录；CASE可选：
                file pod process terminal ack budget expired boundary concurrency nfs command migration all
  joint         三类运维Replay；要求已有Milvus/OpenSearch及缓存模型正常运行
EOF
    ;;
  install)
    bash hack/install-tools.sh
    uv sync --frozen --project agentd --python 3.12 --all-extras
    ;;
  check)
    check_cluster
    "$python_bin" --version
    go version
    docker version --format '{{.Server.Version}}'
    printf '项目NFS地址：%s\n' "$(nfs_address)"
    kubectl --context kind-sandboxd get runtimeclass gvisor
    ;;
  build)
    [[ "$(go version)" == 'go version go1.26.5 '* ]] || { echo '需要Go1.26.5。' >&2; exit 1; }
    mkdir -p .cache
    GOMODCACHE="${GOMODCACHE:-/tmp/sandboxd-workspace-go-mod}" GOCACHE="${GOCACHE:-/tmp/sandboxd-workspace-go-build}" \
      go build -o "$binary" ./cmd/sandboxd
    ;;
  nfs)
    check_cluster
    : "${SANDBOXD_NFS_IMAGE:?设置已核实的固定NFS镜像ID或digest}"
    # 保留既有容器/IP和named volumes；升级存储服务另走显式迁移。
    docker compose -f deploy/workspace/compose.yml up -d --no-recreate nfs
    ;;
  go)
    check_cluster
    : "${SANDBOXD_TOKEN:?需要Agent Token}" "${SANDBOXD_OPERATOR_TOKEN:?需要独立Operator Token}"
    : "${SANDBOXD_WORKSPACE_CONTROL_DIR:?设置原生Linux持久控制目录}"
    backup_args=()
    case "${AGENTD_CHECKPOINT_MODE:-off}" in
      off) ;;
      restic)
        : "${SANDBOXD_BACKUP_SECRET_NAME:?设置已初始化专属仓库的Secret名称}"
        : "${SANDBOXD_BACKUP_MINIO_IP:?设置已核对的专属MinIO IPv4}"
        backup_args=(-workspace-backup-image sandboxd-restic:0.18.1-v2 \
                     -workspace-backup-secret "$SANDBOXD_BACKUP_SECRET_NAME" \
                     -workspace-backup-minio-ip "$SANDBOXD_BACKUP_MINIO_IP")
        ;;
      *) echo 'AGENTD_CHECKPOINT_MODE只能为off或restic。' >&2; exit 1 ;;
    esac
    exec "$binary" -listen 127.0.0.1:8080 -pool-size 0 -workspace-nfs-server "$(nfs_address)" \
      -workspace-image sandboxd-workspace:runtime-v1.5 -workspace-control-dir "$SANDBOXD_WORKSPACE_CONTROL_DIR" "${backup_args[@]}"
    ;;
  agent)
    : "${AGENTD_TOKEN:?需要Agent API Token}" "${AGENTD_ALERT_TOKEN:?需要独立Alert Token}" "${SANDBOXD_TOKEN:?需要sandboxd Agent Token}"
    : "${AGENTD_RECOVERY_DIR:?设置原生Linux持久控制目录}"
    [[ "${AGENTD_LLM_MODE:-replay}" == replay ]] || { echo '本入口只允许Replay。' >&2; exit 1; }
    unset SANDBOXD_OPERATOR_TOKEN
    export AGENTD_WORKSPACE_BACKEND=nfs AGENTD_LLM_MODE=replay AGENTD_ROUTER_MODE=off
    exec "$python_bin" -m uvicorn agentd.app.main:create_app --factory --host 127.0.0.1 --port 8090
    ;;
  test)
    exec "$python_bin" -m unittest discover -s agentd/tests -v
    ;;
  migrate)
    unset SANDBOXD_OPERATOR_TOKEN
    exec "$python_bin" -m agentd.workspace_migrate "$@"
    ;;
  entry)
    check_cluster
    nfs_address >/dev/null
    [[ -z "$(ss -ltnH '( sport = :8080 or sport = :8090 )')" ]] || { echo '入口实验要求8080/8090空闲。' >&2; exit 1; }
    exec "$python_bin" -m agentd.workspace_entry_demo "$@"
    ;;
  benchmark)
    check_cluster
    server="$(nfs_address)"
    [[ -z "$(ss -ltnH '( sport = :10880 )')" ]] || { echo '性能实验要求10880空闲。' >&2; exit 1; }
    exec "$python_bin" -m agentd.workspace_benchmark --nfs-server "$server" "$@"
    ;;
  joint)
    # 不替用户启动数据库、重建索引或下载模型；缺少依赖时明确失败。
    export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=2
    exec "$python_bin" -m agentd.ops_demo "$@"
    ;;
  verify)
    scenario="${1:-}"
    case "$scenario" in
      file|pod|process|terminal|ack|budget|expired|boundary|concurrency|nfs|command|migration|all) ;;
      *) echo '缺少或未知CASE，参见help。' >&2; exit 1 ;;
    esac
    check_cluster
    server="$(nfs_address)"
    mkdir -p .cache/workspace-eval
    output_dir="$(mktemp -d "$repo_root/.cache/workspace-eval/run-XXXXXXXX")"
    cases=("$scenario")
    if [[ "$scenario" == all ]]; then
      cases=(file pod process terminal ack budget expired boundary concurrency nfs command migration)
    fi
    for item in "${cases[@]}"; do
      module="workspace_${item}_demo"; options=()
      case "$item" in
        file) module=workspace_demo ;;
        pod) module=workspace_demo; options=(--fault-after-first-turn) ;;
        ack) module=workspace_demo; options=(--lose-write-ack) ;;
        process) module=workspace_process_demo ;;
        expired|budget) module=workspace_budget_demo; options=(--scenario "$item") ;;
        nfs) module=workspace_unavailable_demo ;;
      esac
      printf '执行 %s；结果目录：%s\n' "$item" "$output_dir"
      "$python_bin" -m "agentd.$module" --nfs-server "$server" "${options[@]}" \
        --output "$output_dir/$item.json" > "$output_dir/$item-stdout.txt" 2>&1
    done
    printf '存储验收结束：%s；联合回放与性能测量另执行。\n' "$output_dir"
    ;;
  *) echo '未知ACTION，参见help。' >&2; exit 1 ;;
esac
