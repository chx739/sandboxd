# run-linux-agent-demo.sh 会在构建前校验这个本地 tag 的完整 image ID。
# 直接写 FROM repo@digest 会让 BuildKit 离线时仍尝试解析远端 metadata。
FROM gcr.io/k8s-minikube/kicbase:v0.0.50

# Demo 用户没有 sudo；空密码只用于解除系统账户锁定，sshd 全局禁止密码认证。
RUN useradd --uid 10001 --create-home --shell /bin/sh agentdemo \
    && passwd -d agentdemo \
    && mkdir -p /run/sshd

COPY --chmod=0555 forced-command.sh /usr/local/bin/sandboxd-forced-command
COPY --chmod=0555 entrypoint.sh /usr/local/bin/sandboxd-linux-entrypoint
COPY --chmod=0444 demo.log /var/log/sandboxd-demo.log
COPY --chmod=0444 sshd_config /etc/ssh/sshd_config.sandboxd

EXPOSE 22
ENTRYPOINT ["/usr/local/bin/sandboxd-linux-entrypoint"]
