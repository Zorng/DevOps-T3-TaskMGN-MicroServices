#!/bin/sh
# Start-up wrapper for Identity's database container (used by infra/compose.yaml).
#
# The official PostgreSQL image runs an init script directly when it looks
# executable and reads it otherwise. On Docker Desktop for Mac, a bind-mounted
# file can look executable without being runnable, so the direct run fails with
# "bad interpreter: Permission denied", the identity_app login is never created,
# and PostgreSQL skips initialization on every later start.
#
# Copy Identity's own init script into the container's filesystem as a plain
# non-executable file so it is always read, then continue with Identity's entrypoint.
set -eu

cp /opt/identity/init-db.sh /docker-entrypoint-initdb.d/10-identity.sh
chmod 0644 /docker-entrypoint-initdb.d/10-identity.sh
exec /bin/sh /opt/identity/entrypoint.sh "$@"