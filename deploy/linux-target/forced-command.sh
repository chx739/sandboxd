#!/bin/sh

set -eu

# 即使 Python Connector 被绕过，远端也只识别四个完全匹配的字符串。
# 这里没有 eval，也不把 SSH_ORIGINAL_COMMAND 拼进任何 shell 命令。
case "${SSH_ORIGINAL_COMMAND:-}" in
  host_summary)
    /usr/bin/uname -sr
    /usr/bin/uptime
    /usr/bin/free -m
    ;;
  process_list)
    /usr/bin/ps -eo pid,user,stat,comm --sort=pid | /usr/bin/head -n 21
    ;;
  disk_usage)
    /usr/bin/df -hP -x tmpfs -x devtmpfs | /usr/bin/head -n 20
    ;;
  read_demo_log)
    /usr/bin/tail -n 20 /var/log/sandboxd-demo.log
    ;;
  *)
    echo "operation denied by forced-command" >&2
    exit 126
    ;;
esac
