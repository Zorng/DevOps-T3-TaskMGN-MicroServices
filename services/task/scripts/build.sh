#!/bin/sh
set -eu

# Change to the service root.
cd "$(dirname "$0")/.."

exec docker build -t task-service:local .
