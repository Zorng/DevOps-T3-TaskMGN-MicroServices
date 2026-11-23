#!/usr/bin/env bash
# Back up the broker: topology (definitions) and data (queued messages, users).
# The broker is stopped briefly so the data copy is consistent.
# Treat the output as a secret: the data archive contains user credentials.
#   usage: infra/broker/scripts/backup.sh [output-dir]      (default: infra/broker/backups)
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
out="${1:-backups}"; stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out"

curl -fsS -u "${BROKER_ADMIN_USER}:${BROKER_ADMIN_PASSWORD}" \
  "http://127.0.0.1:${BROKER_MGMT_PORT:-15672}/api/definitions/taskmgn" \
  -o "$out/definitions-$stamp.json"

docker compose -f compose.yaml stop broker
docker run --rm -v taskmgn-broker-data:/data:ro -v "$(cd "$out" && pwd)":/backup alpine \
  tar czf "/backup/data-$stamp.tgz" -C /data .
docker compose -f compose.yaml start broker
echo "backup written to $out (definitions-$stamp.json, data-$stamp.tgz)"
