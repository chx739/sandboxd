# Official SIG Storage registry was unreachable from this environment. Build the
# same Ganesha VFS server from fixed Debian packages, without a provisioner.
FROM debian:bookworm-slim@sha256:a4672c0cb26fbdde88e38fa2dfb6c681942306680e41e4378b28770b6e79ee91
RUN apt-get update \
    && apt-get install -y --no-install-recommends nfs-ganesha=4.3-2 nfs-ganesha-vfs=4.3-2 \
    && mkdir -p /etc/sandboxd /usr/share/sandboxd \
    && dpkg-query -W > /usr/share/sandboxd/package-versions.txt \
    && rm -rf /var/lib/apt/lists/*
COPY ganesha.conf /etc/sandboxd/ganesha.conf
COPY start-nfs.sh /etc/sandboxd/start-nfs.sh
ENTRYPOINT ["/bin/sh", "/etc/sandboxd/start-nfs.sh"]
