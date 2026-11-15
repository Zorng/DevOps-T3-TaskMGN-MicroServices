#!/bin/sh
set -eu

# Change to task-service root.
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "Missing .env: create it using .env.example." >&2
    exit 1
fi

# Read and export your trusted local configuration.
set -a
. ./.env
set +a

# Fail clearly if required values are missing or empty.
: "${TASK_DB_URL:?Set TASK_DB_URL in .env}"
: "${TASK_DB_USERNAME:?Set TASK_DB_USERNAME in .env}"
: "${TASK_DB_PASSWORD:?Set TASK_DB_PASSWORD in .env}"

exec ./mvnw spring-boot:run