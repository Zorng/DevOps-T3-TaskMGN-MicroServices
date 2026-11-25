#!/usr/bin/env bash
# Integration checks for the local application stack (issue #31).
#
# Run from the repository root while the stack is running:
#   bash infra/scripts/check.sh               # read-only checks
#   bash infra/scripts/check.sh --lifecycle   # also stops and restarts parts of the stack
#
# Exits 0 when every check passes and 1 otherwise. Nothing secret is printed.
# Works with the macOS default bash (3.2) as well as current Linux bash.
set -u -o pipefail

LIFECYCLE=0
for argument in "$@"; do
  case "$argument" in
    --lifecycle) LIFECYCLE=1 ;;
    -h | --help)
      echo "Usage: bash infra/scripts/check.sh [--lifecycle]"
      exit 0
      ;;
    *)
      echo "Unknown option: $argument" >&2
      exit 2
      ;;
  esac
done

cd "$(dirname "$0")/../.." || exit 2
COMPOSE=(docker compose -f infra/compose.yaml)

if [ -z "${GATEWAY_HTTP_PORT:-}" ] && [ -f infra/.env ]; then
  GATEWAY_HTTP_PORT=$(sed -n 's/^GATEWAY_HTTP_PORT=//p' infra/.env | tail -n 1)
fi
GATEWAY="http://127.0.0.1:${GATEWAY_HTTP_PORT:-8080}"
WORKSPACE_ID=11111111-1111-4111-8111-111111111111

FAILURES=0
STOPPED=""

pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
section() { printf '\n== %s ==\n' "$1"; }

expect() { # description expected actual
  if [ "$3" = "$2" ]; then pass "$1 (got $3)"; else fail "$1 (expected $2, got $3)"; fi
}

# Wait up to <seconds> for "<command...>" to print <expected>, then record the result.
wait_for() { # description expected seconds command...
  local description=$1 expected=$2 seconds=$3 actual="" waited=0
  shift 3
  while [ "$waited" -lt "$seconds" ]; do
    actual=$("$@")
    [ "$actual" = "$expected" ] && break
    sleep 1
    waited=$((waited + 1))
  done
  expect "$description" "$expected" "$actual"
}

# Restart anything this script stopped, even if it is interrupted.
cleanup() {
  local service
  for service in $STOPPED; do
    "${COMPOSE[@]}" start "$service" >/dev/null 2>&1
  done
}
trap cleanup EXIT

# --- Helpers ------------------------------------------------------------------

# HTTP status of a request to the public gateway (000 if it cannot connect).
gateway_code() { # method path
  curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X "$1" "$GATEWAY$2" || true
}

# HTTP status of a request made from inside the private network (0 if unreachable).
PROBE='import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen(sys.argv[1], timeout=5).status)
except urllib.error.HTTPError as error:
    print(error.code)
except Exception:
    print(0)'
inside_code() { # url
  local code
  code=$("${COMPOSE[@]}" exec -T identity-workspace python -c "$PROBE" "$1" 2>/dev/null | tr -d '\r\n ')
  echo "${code:-0}"
}

# Can a service open a TCP connection to host:port? Exit status 0 means yes.
CONNECT='import socket, sys
socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3).close()'
python_can_connect() { # service host port
  "${COMPOSE[@]}" exec -T "$1" python -c "$CONNECT" "$2" "$3" >/dev/null 2>&1
}
task_can_connect() { # host port
  "${COMPOSE[@]}" exec -T task bash -c 'exec 3<>/dev/tcp/"$0"/"$1"' "$1" "$2" >/dev/null 2>&1
}

container_id() { "${COMPOSE[@]}" ps -q "$1" 2>/dev/null | head -n 1; }

networks_of() { # service -> sorted, space-separated network names
  local id
  id=$(container_id "$1")
  if [ -z "$id" ]; then echo "(not running)"; return; fi
  docker inspect -f '{{range $name, $net := .NetworkSettings.Networks}}{{$name}} {{end}}' "$id" \
    | tr ' ' '\n' | sed '/^$/d' | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//'
}

state_of() { # service -> running, exited, ...
  local id
  id=$(container_id "$1")
  [ -n "$id" ] && docker inspect -f '{{.State.Status}}' "$id" 2>/dev/null || echo "missing"
}

health_of() { # service -> healthy, unhealthy, starting
  local id
  id=$(container_id "$1")
  [ -n "$id" ] && docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo "missing"
}

# Identifies a PostgreSQL data directory; it changes only if the volume is recreated.
database_id() { # service
  "${COMPOSE[@]}" exec -T "$1" sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select system_identifier from pg_control_system()"' \
    2>/dev/null | tr -d '\r\n '
}

# Hash of RabbitMQ's randomly generated Erlang cookie, which lives on the broker's
# data volume. It changes only if the volume is recreated. The value is compared
# but never printed.
broker_id() {
  "${COMPOSE[@]}" exec -T broker sh -c 'sha256sum /var/lib/rabbitmq/.erlang.cookie' 2>/dev/null | cut -d' ' -f1
}

if [ -z "$(container_id gateway)" ]; then
  echo "The stack is not running. Start it first:" >&2
  echo "  docker compose -f infra/compose.yaml up -d --build --wait" >&2
  exit 2
fi

# --- 1. Public gateway ----------------------------------------------------------
section "Public gateway ($GATEWAY)"
expect "gateway liveness /healthz" 200 "$(gateway_code GET /healthz)"
expect "Identity route reaches Identity (no login -> 401)" 401 "$(gateway_code GET /api/v1/me)"
expect "Task route reaches Task (no login -> 401)" 401 "$(gateway_code GET "/api/v1/workspaces/$WORKSPACE_ID/boards")"
# Notification is not built yet, so 503 is the correct answer today. When that
# service joins the stack, change this to the status an unauthenticated request gets.
expect "Notification route answers 503 (service not built yet)" 503 "$(gateway_code GET /api/v1/notifications)"
expect "unknown path is 404" 404 "$(gateway_code GET /no-such-route)"
for path in /health/live /health/ready /metrics /actuator/health /actuator/prometheus; do
  expect "private path $path is not public" 404 "$(gateway_code GET "$path")"
done
for path in /internal/v1/auth/introspect "/internal/v1/workspaces/$WORKSPACE_ID/access"; do
  expect "internal API POST $path is not public" 404 "$(gateway_code POST "$path")"
done

# --- 2. Health probes -------------------------------------------------------------
section "Health probes (from inside the private network)"
expect "Identity liveness" 200 "$(inside_code http://127.0.0.1:8000/health/live)"
expect "Identity readiness" 200 "$(inside_code http://127.0.0.1:8000/health/ready)"
expect "Task liveness" 200 "$(inside_code http://task:8001/health/live)"
expect "Task readiness (needs its database and Identity)" 200 "$(inside_code http://task:8001/health/ready)"

# --- 3. Isolation -----------------------------------------------------------------
section "Private networking"
PUBLISHED=$("${COMPOSE[@]}" ps --format json 2>/dev/null | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
rows = json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines() if line.strip()]
for row in rows:
    for port in row.get("Publishers") or []:
        if port.get("PublishedPort"):
            print(row.get("Service") or row.get("Name"), port.get("URL"), port.get("PublishedPort"))
' | sort)
expect "only the gateway publishes a host port, on localhost only" \
  "gateway 127.0.0.1 ${GATEWAY_HTTP_PORT:-8080}" "$PUBLISHED"

expect "gateway network" "taskmgn_backend" "$(networks_of gateway)"
expect "Identity database network" "taskmgn_identity-data" "$(networks_of identity-db)"
expect "Task database network" "taskmgn_task-data" "$(networks_of task-db)"
expect "broker network" "taskmgn_messaging" "$(networks_of broker)"
for network in identity-data task-data messaging; do
  expect "network $network blocks outside traffic" true \
    "$(docker network inspect -f '{{.Internal}}' "taskmgn_$network" 2>/dev/null || echo missing)"
done

if python_can_connect identity-workspace task-db 5432; then fail "Identity can reach Task's database"; else pass "Identity cannot reach Task's database"; fi
if python_can_connect identity-workspace broker 5672; then fail "Identity API can reach the broker"; else pass "Identity API cannot reach the broker (only its worker can)"; fi
if task_can_connect identity-db 5432; then fail "Task can reach Identity's database"; else pass "Task cannot reach Identity's database"; fi
if task_can_connect broker 5672; then pass "Task can reach the broker (its outbox publisher needs it)"; else fail "Task cannot reach the broker"; fi
if python_can_connect identity-outbox broker 5672; then pass "Identity's outbox worker can reach the broker"; else fail "Identity's outbox worker cannot reach the broker"; fi
expect "Identity outbox worker is running" running "$(state_of identity-outbox)"

# --- 4. Lifecycle (stops and restarts services) ----------------------------------
if [ "$LIFECYCLE" = 1 ]; then
  section "Broker outage: readiness must not depend on the broker"
  "${COMPOSE[@]}" stop broker >/dev/null 2>&1
  STOPPED="$STOPPED broker"
  sleep 5
  expect "Identity ready while the broker is stopped" 200 "$(inside_code http://127.0.0.1:8000/health/ready)"
  expect "Task ready while the broker is stopped" 200 "$(inside_code http://task:8001/health/ready)"
  expect "gateway still serves Identity routes" 401 "$(gateway_code GET /api/v1/me)"
  "${COMPOSE[@]}" start broker >/dev/null 2>&1
  STOPPED=""
  wait_for "broker healthy again" healthy 90 health_of broker
  expect "Identity outbox worker survived the outage" running "$(state_of identity-outbox)"

  section "Identity database outage: readiness fails, liveness stays up"
  "${COMPOSE[@]}" stop identity-db >/dev/null 2>&1
  STOPPED="$STOPPED identity-db"
  wait_for "Identity readiness is 503 without its database" 503 40 inside_code http://127.0.0.1:8000/health/ready
  expect "Identity liveness stays 200" 200 "$(inside_code http://127.0.0.1:8000/health/live)"
  wait_for "Task readiness is 503 when Identity is not ready" 503 40 inside_code http://task:8001/health/ready
  expect "Task liveness stays 200" 200 "$(inside_code http://task:8001/health/live)"
  "${COMPOSE[@]}" start identity-db >/dev/null 2>&1
  STOPPED=""
  wait_for "Identity ready again after recovery" 200 90 inside_code http://127.0.0.1:8000/health/ready
  wait_for "Task ready again after recovery" 200 90 inside_code http://task:8001/health/ready

  section "Restarting services: the gateway must keep reaching them"
  "${COMPOSE[@]}" restart identity-workspace task >/dev/null 2>&1
  wait_for "Identity ready after restart" 200 90 inside_code http://127.0.0.1:8000/health/ready
  wait_for "Task ready after restart" 200 120 inside_code http://task:8001/health/ready
  expect "gateway reaches Identity after restart" 401 "$(gateway_code GET /api/v1/me)"
  expect "gateway reaches Task after restart" 401 "$(gateway_code GET "/api/v1/workspaces/$WORKSPACE_ID/boards")"

  section "Stop and start the whole stack: data must survive"
  BEFORE_IDENTITY=$(database_id identity-db)
  BEFORE_TASK=$(database_id task-db)
  BEFORE_BROKER=$(broker_id)
  [ -n "$BEFORE_IDENTITY" ] && pass "recorded Identity database identity" || fail "could not read Identity database identity"
  [ -n "$BEFORE_TASK" ] && pass "recorded Task database identity" || fail "could not read Task database identity"
  [ -n "$BEFORE_BROKER" ] && pass "recorded broker data identity" || fail "could not read broker data identity"
  "${COMPOSE[@]}" down >/dev/null 2>&1
  if "${COMPOSE[@]}" up -d --wait >/dev/null 2>&1; then pass "stack started again after down"; else fail "stack did not start again after down"; fi
  expect "Identity database kept its data" "$BEFORE_IDENTITY" "$(database_id identity-db)"
  expect "Task database kept its data" "$BEFORE_TASK" "$(database_id task-db)"
  if [ -n "$BEFORE_BROKER" ] && [ "$(broker_id)" = "$BEFORE_BROKER" ]; then pass "broker kept its data"; else fail "broker did not keep its data"; fi
  wait_for "Identity ready after full restart" 200 90 inside_code http://127.0.0.1:8000/health/ready
  wait_for "Task ready after full restart" 200 120 inside_code http://task:8001/health/ready
  expect "gateway serves Identity routes after full restart" 401 "$(gateway_code GET /api/v1/me)"
fi

# --- Summary ------------------------------------------------------------------------
section "Not verified by this script (incomplete integrations)"
cat <<'NOTES'
  - Notification service: not built; its routes answer 503 by design.
  - Sign-in and session/workspace checks through the gateway: need a real Google
    client ID and a browser sign-in (manual; see infra/README.md).
  - Event consumers: Notification and Task's lifecycle-event cleanup are not
    implemented, so no event is consumed or acted on yet. Routing from the
    producers to the broker queues is covered by check-events.sh.
NOTES
[ "$LIFECYCLE" = 1 ] || echo "  - Outage, restart and data-persistence checks: re-run with --lifecycle."

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "All checks passed."
  exit 0
fi
echo "$FAILURES check(s) failed."
exit 1