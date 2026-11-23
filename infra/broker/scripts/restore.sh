#!/usr/bin/env bash
# Restore the broker data volume from a backup made by backup.sh. This REPLACES
# everything currently stored in the broker.
#   usage: infra/broker/scripts/restore.sh backups/data-YYYYMMDD-HHMMSS.tgz
set -euo pipefail
cd "$(dirname "$0")/.."
archive="${1:?usage: restore.sh <data-archive.tgz>}"
dir="$(cd "$(dirname "$archive")" && pwd)"; file="$(basename "$archive")"

docker compose -f compose.yaml stop broker
docker run --rm -v taskmgn-broker-data:/data -v "$dir":/backup alpine \
  sh -c "rm -rf /data/* /data/.[!.]* 2>/dev/null; tar xzf /backup/$file -C /data"
docker compose -f compose.yaml start broker
echo "restored from $archive; wait for the broker to become healthy, then run the verification commands in the README"
