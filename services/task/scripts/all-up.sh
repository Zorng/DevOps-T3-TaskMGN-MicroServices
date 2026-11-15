#!/bin/sh
set -eu

# Change to the service root.
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "Missing .env: copy .env.example to .env and set TASK_DB_PASSWORD." >&2
    exit 1
fi

exec docker compose --profile app up --build -d --wait
