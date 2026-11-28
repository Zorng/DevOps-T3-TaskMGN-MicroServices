#!/usr/bin/env bash
# Event-routing checks for the local application stack (issue #31).
#
# Proves, with the real broker and the real outbox publishers of Identity and Task,
# that every event type in the contract reaches its durable queue as a persistent
# message with the right ID and type. Run from the repository root:
#   bash infra/scripts/check-events.sh            # routing checks (about 20 seconds)
#   bash infra/scripts/check-events.sh --outage   # also: events wait out a broker outage
#
# How it works: the contract's example events (api_contracts/examples/events.json)
# are given fresh IDs and written to each producer's own outbox table. The producer's
# own publisher sends them. Notification consumes notifications.v1, so for those
# events the check looks at its effect: exactly one notification and one
# processed-event marker per event in Notification's database. Nothing consumes
# task-cleanup.v1 in this stack, so a probe reads those events back from the queue
# and acknowledges only them. Seeded rows are deleted at the end.
#
# Events are seeded directly because creating them through the public APIs needs a
# Google sign-in.
#
# Exits 0 when every check passes and 1 otherwise. Secrets are never printed.
# Works with the macOS default bash (3.2) as well as current Linux bash.
set -u -o pipefail

OUTAGE=0
for argument in "$@"; do
  case "$argument" in
    --outage) OUTAGE=1 ;;
    -h | --help)
      echo "Usage: bash infra/scripts/check-events.sh [--outage]"
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

# type | producer (whose outbox the event is written to) | queue that must receive it
EVENTS=(
  "invitation.created.v1|identity|notifications.v1"
  "workspace.member_removed.v1|identity|task-cleanup.v1"
  "workspace.deleted.v1|identity|task-cleanup.v1"
  "task.assigned.v1|task|notifications.v1"
  "task.comment_mentioned.v1|task|notifications.v1"
)

FAILURES=0
STOPPED=""
IDS=()
PRODUCERS=()

pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
section() { printf '\n== %s ==\n' "$1"; }

expect() { # description expected actual
  if [ "$3" = "$2" ]; then pass "$1 (got $3)"; else fail "$1 (expected $2, got $3)"; fi
}

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

if [ ! -f infra/.env ]; then
  echo "infra/.env is missing. Run: python3 infra/scripts/setup_env.py" >&2
  exit 2
fi
env_value() { sed -n "s/^$1=//p" infra/.env | tail -n 1; }
BROKER_ADMIN_USER=$(env_value BROKER_ADMIN_USER)
BROKER_ADMIN_PASSWORD=$(env_value BROKER_ADMIN_PASSWORD)

container_id() { "${COMPOSE[@]}" ps -q "$1" 2>/dev/null | head -n 1; }
if [ -z "$(container_id broker)" ] || [ -z "$(container_id identity-outbox)" ] || [ -z "$(container_id notification)" ]; then
  echo "The stack is not running. Start it first:" >&2
  echo "  docker compose -f infra/compose.yaml up -d --build --wait" >&2
  exit 2
fi

health_of() {
  local id
  id=$(container_id "$1")
  [ -n "$id" ] && docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo "missing"
}

PROBE_HTTP='import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen(sys.argv[1], timeout=5).status)
except urllib.error.HTTPError as error:
    print(error.code)
except Exception:
    print(0)'
inside_code() { # url
  local code
  code=$("${COMPOSE[@]}" exec -T identity-workspace python -c "$PROBE_HTTP" "$1" 2>/dev/null | tr -d '\r\n ')
  echo "${code:-0}"
}

new_uuid() { python3 -c 'import uuid; print(uuid.uuid4())'; }

# Contract example for <type>, with a fresh event ID, request ID and time.
event_json() { # type event_id request_id
  python3 - "$1" "$2" "$3" <<'PY'
import datetime, json, sys
event_type, event_id, request_id = sys.argv[1:4]
with open("api_contracts/examples/events.json", encoding="utf-8") as stream:
    examples = json.load(stream)
event = next(item for item in examples if item["type"] == event_type)
event["id"] = event_id
event["request_id"] = request_id
event["occurred_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print(json.dumps(event, separators=(",", ":")))
PY
}

# Run one SQL statement in a service's own database container. The statement is
# passed as an argument ($0), so no quoting problems reach the container's shell.
run_sql() { # service sql
  "${COMPOSE[@]}" exec -T "$1" sh -c \
    'psql -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$0"' "$2" \
    2>/dev/null | tr -d '\r\n'
}

db_of() { if [ "$1" = identity ]; then echo identity-db; else echo task-db; fi; }

seed() { # producer type id request_id
  local payload sql
  payload=$(event_json "$2" "$3" "$4") || return 1
  if [ "$1" = identity ]; then
    sql="INSERT INTO identity_workspace.outbox (event_id, event_type, payload) VALUES ('$3', '$2', '$payload'::jsonb)"
  else
    sql="INSERT INTO task_service.outbox (id, event_type, payload, created_at) VALUES ('$3', '$2', '$payload'::jsonb, now())"
  fi
  run_sql "$(db_of "$1")" "$sql" >/dev/null
}

# "<notifications>/<markers>" stored by Notification for one event ID.
notification_effect() { # id
  run_sql notification-db "SELECT (SELECT count(*) FROM notification_service.notifications WHERE event_id = '$1') || '/' || (SELECT count(*) FROM notification_service.processed_events WHERE event_id = '$1')"
}

delivery_state() { # producer id -> published | pending | (empty when the row is missing)
  local table column
  if [ "$1" = identity ]; then table=identity_workspace.outbox column=event_id; else table=task_service.outbox column=id; fi
  run_sql "$(db_of "$1")" \
    "SELECT CASE WHEN published_at IS NULL THEN 'pending' ELSE 'published' END FROM $table WHERE $column = '$2'"
}

purge_seeded() { # delete the rows this script wrote, whatever their state
  local index=0 table column
  [ "${#IDS[@]}" -gt 0 ] || return 0
  while [ "$index" -lt "${#IDS[@]}" ]; do
    if [ "${PRODUCERS[$index]}" = identity ]; then table=identity_workspace.outbox column=event_id; else table=task_service.outbox column=id; fi
    run_sql "$(db_of "${PRODUCERS[$index]}")" "DELETE FROM $table WHERE $column = '${IDS[$index]}'" >/dev/null
    run_sql notification-db "DELETE FROM notification_service.notifications WHERE event_id = '${IDS[$index]}'" >/dev/null
    run_sql notification-db "DELETE FROM notification_service.processed_events WHERE event_id = '${IDS[$index]}'" >/dev/null
    index=$((index + 1))
  done
  IDS=()
  PRODUCERS=()
}

cleanup() {
  [ -n "$STOPPED" ] && "${COMPOSE[@]}" start broker >/dev/null 2>&1
  purge_seeded
}
trap cleanup EXIT

depth() { # queue -> number of messages in it
  "${COMPOSE[@]}" exec -T broker rabbitmqctl -q list_queues -p taskmgn name messages 2>/dev/null \
    | tr -d '\r' | awk -v queue="$1" '$1 == queue { print $2 }'
}

# RabbitMQ refreshes its queue statistics every few seconds, so a reading taken right
# after messages are acknowledged can be stale. settled_depth waits until the reading
# stops changing; the final checks below wait for the expected value instead.
settled_depth() { # queue -> depth once three readings in a row agree
  local queue=$1 last="" now="" same=0 tries=0
  while [ "$tries" -lt 30 ] && [ "$same" -lt 2 ]; do
    now=$(depth "$queue")
    if [ -n "$now" ] && [ "$now" = "$last" ]; then same=$((same + 1)); else same=0; fi
    last=$now
    tries=$((tries + 1))
    [ "$same" -lt 2 ] && sleep 2
  done
  echo "$last"
}

# Reads each expected message from its queue, checks it and acknowledges only that
# message. Other messages are put back. Credentials arrive on stdin, never in argv.
PROBE_PY='import json, sys
import pika
user = sys.stdin.readline().rstrip("\n")
password = sys.stdin.readline().rstrip("\n")
wanted = json.loads(sys.argv[1])
parameters = pika.ConnectionParameters(
    host="broker", port=5672, virtual_host="taskmgn",
    credentials=pika.PlainCredentials(user, password),
    socket_timeout=5, blocked_connection_timeout=5, connection_attempts=1)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()
results = {}
for queue in sorted({item["queue"] for item in wanted}):
    last_other = None
    for _ in range(500):
        method, properties, body = channel.basic_get(queue, auto_ack=False)
        if method is None:
            break
        hits = [item for item in wanted if item["queue"] == queue and item["id"] == properties.message_id]
        if not hits:
            last_other = method.delivery_tag
            continue
        item = hits[0]
        try:
            document = json.loads(body)
        except ValueError:
            document = {}
        problems = []
        if properties.delivery_mode != 2:
            problems.append("not persistent")
        if properties.type != item["type"]:
            problems.append("wrong type header")
        if document.get("id") != item["id"] or document.get("type") != item["type"]:
            problems.append("body does not match")
        results[item["id"]] = problems
        channel.basic_ack(method.delivery_tag)
    if last_other is not None:
        channel.basic_nack(last_other, multiple=True, requeue=True)
connection.close()
for item in wanted:
    if item["id"] not in results:
        print("RESULT %s missing" % item["id"])
    elif results[item["id"]]:
        print("RESULT %s problems: %s" % (item["id"], ", ".join(results[item["id"]])))
    else:
        print("RESULT %s ok" % item["id"])'

QUEUES=()
TYPES=()

verify_consumed() { # wait for Notification's effect of each notifications.v1 event
  local index=0
  while [ "$index" -lt "${#IDS[@]}" ]; do
    if [ "${QUEUES[$index]}" = notifications.v1 ]; then
      wait_for "${TYPES[$index]} consumed by Notification (one notification, one marker)" 1/1 "$1" \
        notification_effect "${IDS[$index]}"
    fi
    index=$((index + 1))
  done
}

verify_in_queues() {
  local wanted="" index=0 output result
  while [ "$index" -lt "${#IDS[@]}" ]; do
    if [ "${QUEUES[$index]}" != notifications.v1 ]; then
      [ -n "$wanted" ] && wanted="$wanted,"
      wanted="$wanted{\"queue\":\"${QUEUES[$index]}\",\"id\":\"${IDS[$index]}\",\"type\":\"${TYPES[$index]}\"}"
    fi
    index=$((index + 1))
  done
  output=$(printf '%s\n%s\n' "$BROKER_ADMIN_USER" "$BROKER_ADMIN_PASSWORD" \
    | "${COMPOSE[@]}" exec -T identity-outbox python -c "$PROBE_PY" "[$wanted]" 2>&1)
  index=0
  while [ "$index" -lt "${#IDS[@]}" ]; do
    if [ "${QUEUES[$index]}" = notifications.v1 ]; then
      index=$((index + 1))
      continue
    fi
    result=$(printf '%s\n' "$output" | sed -n "s/^RESULT ${IDS[$index]} //p")
    if [ "$result" = ok ]; then
      pass "${TYPES[$index]} arrived in ${QUEUES[$index]} (persistent, ID and type match)"
    else
      fail "${TYPES[$index]} in ${QUEUES[$index]}: ${result:-the probe could not read the broker}"
    fi
    index=$((index + 1))
  done
}

run_scenario() { # title outage(0|1)
  local title=$1 outage=$2 spec type producer queue id request_id index base_n base_c wait_seconds=60
  IDS=()
  PRODUCERS=()
  QUEUES=()
  TYPES=()
  section "$title"
  base_n=$(settled_depth notifications.v1)
  base_c=$(settled_depth task-cleanup.v1)
  [ -n "$base_n" ] && [ -n "$base_c" ] && pass "queues readable (notifications.v1: $base_n, task-cleanup.v1: $base_c)" \
    || fail "could not read queue depths from the broker"

  if [ "$outage" = 1 ]; then
    "${COMPOSE[@]}" stop broker >/dev/null 2>&1
    STOPPED=broker
    sleep 3
    wait_seconds=180
  fi

  for spec in "${EVENTS[@]}"; do
    IFS='|' read -r type producer queue <<<"$spec"
    id=$(new_uuid)
    request_id=$(new_uuid)
    if seed "$producer" "$type" "$id" "$request_id"; then
      IDS+=("$id")
      PRODUCERS+=("$producer")
      QUEUES+=("$queue")
      TYPES+=("$type")
      pass "wrote $type to the $producer outbox"
    else
      fail "could not write $type to the $producer outbox"
    fi
  done

  if [ "$outage" = 1 ]; then
    sleep 8
    index=0
    while [ "$index" -lt "${#IDS[@]}" ]; do
      expect "${TYPES[$index]} stays pending while the broker is down" pending "$(delivery_state "${PRODUCERS[$index]}" "${IDS[$index]}")"
      index=$((index + 1))
    done
    expect "Identity stays ready during the broker outage" 200 "$(inside_code http://127.0.0.1:8000/health/ready)"
    expect "Task stays ready during the broker outage" 200 "$(inside_code http://task:8001/health/ready)"
    expect "Notification stays ready during the broker outage" 200 "$(inside_code http://notification:8002/health/ready)"
    "${COMPOSE[@]}" start broker >/dev/null 2>&1
    STOPPED=""
    wait_for "broker healthy again" healthy 90 health_of broker
  fi

  index=0
  while [ "$index" -lt "${#IDS[@]}" ]; do
    wait_for "${TYPES[$index]} marked published by its own publisher" published "$wait_seconds" \
      delivery_state "${PRODUCERS[$index]}" "${IDS[$index]}"
    index=$((index + 1))
  done

  if [ "${#IDS[@]}" -gt 0 ]; then
    verify_consumed "$wait_seconds"
    verify_in_queues
  fi

  wait_for "notifications.v1 is back to its starting depth" "$base_n" 30 depth notifications.v1
  wait_for "task-cleanup.v1 is back to its starting depth" "$base_c" 30 depth task-cleanup.v1
  wait_for "notifications.v1.dlq is empty" 0 30 depth notifications.v1.dlq
  wait_for "task-cleanup.v1.dlq is empty" 0 30 depth task-cleanup.v1.dlq
  purge_seeded
  pass "removed the seeded outbox rows"
}

run_scenario "Event routing: producers to durable queues" 0
if [ "$OUTAGE" = 1 ]; then
  run_scenario "Broker outage: events wait in the outbox, then are delivered" 1
fi

section "Not verified by this script (incomplete integrations)"
cat <<'NOTES'
  - Events were seeded into the outboxes with the contract's example payloads.
    Creating them through the public APIs needs a Google sign-in (manual).
  - Task's lifecycle-event cleanup consumer is not enabled in this stack, so
    task-cleanup.v1 events are only checked in the queue, not acted on.
  - Duplicate delivery, retries and dead-lettering are covered by
    Notification's own broker tests (services/notification/test/broker).
NOTES
[ "$OUTAGE" = 1 ] || echo "  - Broker outage delivery: re-run with --outage."

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "All checks passed."
  exit 0
fi
echo "$FAILURES check(s) failed."
exit 1