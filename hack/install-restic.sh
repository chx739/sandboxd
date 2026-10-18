#!/usr/bin/env bash
set -Eeuo pipefail
restic_version=0.18.1
restic_archive_sha=680838f19d67151adba227e1570cdd8af12c19cf1735783ed1ba928bc41f363d
restic_sums_sha=517eafa3c5ca0b8dcfdc5bcfd870f32315949f282a376a709d68f465ce8bb8c9
restic_binary_sha=01143daba61a1dc8afb0cb3d03ba83e6b118f55b6b146b27473a09efc3df13d6
[[ "$(uname -m)" == x86_64 ]] || { echo 'Only verified linux/amd64.' >&2; exit 1; }
restic_download="$HOME/.local/share/sandboxd-tools/downloads/restic-$restic_version"
restic_install="$HOME/.local/share/sandboxd-tools/restic-$restic_version"
mkdir -p "$restic_download" "$restic_install"
for restic_asset in SHA256SUMS "restic_${restic_version}_linux_amd64.bz2"; do
  if [[ ! -f "$restic_download/$restic_asset" ]]; then
    curl -fL --retry 2 -o "$restic_download/$restic_asset" "https://github.com/restic/restic/releases/download/v$restic_version/$restic_asset"
  fi
done
printf '%s  %s\n' "$restic_sums_sha" "$restic_download/SHA256SUMS" | sha256sum -c --status
printf '%s  %s\n' "$restic_archive_sha" "$restic_download/restic_${restic_version}_linux_amd64.bz2" | sha256sum -c --status
grep -F "$restic_archive_sha  restic_${restic_version}_linux_amd64.bz2" "$restic_download/SHA256SUMS" >/dev/null
bzip2 -dc "$restic_download/restic_${restic_version}_linux_amd64.bz2" > "$restic_install/restic"
printf '%s  %s\n' "$restic_binary_sha" "$restic_install/restic" | sha256sum -c --status
chmod 0755 "$restic_install/restic"
"$restic_install/restic" version
