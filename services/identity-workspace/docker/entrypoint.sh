#!/bin/sh
set -eu

# Read the host-owned secret before the official entrypoint switches to postgres.
# This supports private (0600) secret files on native Linux as well as Docker Desktop.
export IDENTITY_APP_PASSWORD="$(cat "$IDENTITY_APP_PASSWORD_FILE")"
exec /usr/local/bin/docker-entrypoint.sh "$@"
