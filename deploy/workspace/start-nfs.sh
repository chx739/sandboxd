#!/bin/sh
set -eu
# Only initialize an absent project-owned root. Do not recursively chmod/chown
# old workspaces or apply a cleanup/retention policy while starting the server.
if [ ! -e /export/workspaces ]; then
    mkdir -m 0711 /export/workspaces
    chown 65532:65532 /export/workspaces
fi
if [ ! -d /export/workspaces ] || [ -L /export/workspaces ]; then
    echo 'NFS workspace root is not a plain directory' >&2
    exit 1
fi
# root_squash maps kubelet's root lookup to an anonymous UID. The export root
# needs traverse-only access for subdirectory mounts; children remain 0700.
chmod 0711 /export/workspaces
exec /usr/bin/ganesha.nfsd -F -L STDOUT -f /etc/sandboxd/ganesha.conf -p /run/ganesha.pid
