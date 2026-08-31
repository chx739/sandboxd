#!/bin/sh

set -eu

# Key 由宿主脚本一次性生成并只读挂载；镜像和 Git 都不携带任何固定凭据。
test -f /keys/host_key
test -f /keys/authorized_keys
test "$(stat -c '%a' /keys/host_key)" = "600"
test "$(stat -c '%a' /keys/authorized_keys)" = "600"
mkdir -m 0755 -p /run/sshd
# sshd 读取用户公钥前会降权；只复制授权公钥，私钥仍留在 0700 只读挂载中。
cp /keys/authorized_keys /run/sshd/authorized_keys
chown 10001:10001 /run/sshd/authorized_keys
chmod 0600 /run/sshd/authorized_keys

exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config.sandboxd
