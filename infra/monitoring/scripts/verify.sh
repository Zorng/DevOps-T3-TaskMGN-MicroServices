#!/usr/bin/env bash
# Loads the monitoring stack with sample telemetry and checks metrics, alerts, logs,
# correlation, redaction and dashboards. Run from the repository root. KEEP=1 leaves it running.
set -euo pipefail

cd "$(dirname "$0")/.."
export GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-verify-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')}"
export TRACE_REQUEST_ID="${TRACE_REQUEST_ID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"
export TRACE_EVENT_ID="${TRACE_EVENT_ID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"
PROM="http://127.0.0.1:${PROMETHEUS_PORT:-9090}"
LOKI="http://127.0.0.1:${LOKI_PORT:-3100}"
GRAFANA="http://127.0.0.1:${GRAFANA_PORT:-3000}"
compose=(docker compose -p taskmgn-monitoring-verify -f compose.yaml -f compose.sample.yaml)
failures=0

cleanup() {
  if [[ "${KEEP:-0}" != 1 ]]; then "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

check() {
  if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; failures=$((failures + 1)); fi
}

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

promq() { curl -sfG "$PROM/api/v1/query" --data-urlencode "query=$1" | json "len(d['data']['result'])"; }

lokiq() {
  curl -sfG "$LOKI/loki/api/v1/query_range" --data-urlencode "query=$1" \
    --data-urlencode "start=$(( $(date +%s) - 900 ))000000000" --data-urlencode "limit=500"
}

wait_for() {
  local deadline=$((SECONDS + $2))
  until eval "$1" >/dev/null 2>&1; do
    if (( SECONDS > deadline )); then return 1; fi
    sleep 3
  done
}

echo "Starting monitoring with sample telemetry (request $TRACE_REQUEST_ID, event $TRACE_EVENT_ID)"
"${compose[@]}" up -d --wait --quiet-pull
check "Loki is ready" 'wait_for "curl -sf $LOKI/ready" 120'

wait_for '[[ $(curl -sf "$PROM/api/v1/targets?state=active" | json "sum(t[\"health\"] == \"up\" for t in d[\"data\"][\"activeTargets\"])") == 5 ]]' 90 || true
check "all five scrape targets are up" \
  '[[ $(curl -sf "$PROM/api/v1/targets?state=active" | json "sum(t[\"health\"] == \"up\" for t in d[\"data\"][\"activeTargets\"])") == 5 ]]'
for query in \
  'identity_readiness_status' 'identity_outbox_oldest_pending_age_seconds' 'task_outbox_oldest_seconds' \
  'notification_consumer_events_total' 'notification_http_request_duration_seconds_bucket' \
  'rabbitmq_queue_messages{queue="notifications.v1.dlq"}'; do
  check "metric present: $query" "[[ \$(promq '$query') -gt 0 ]]"
done

wait_for '[[ $(curl -sf "$PROM/api/v1/alerts" | json "len(d[\"data\"][\"alerts\"])") -ge 2 ]]' 120 || true
for alert in OutboxBacklogOld DeadLettersWaiting; do
  check "alert raised from sample data: $alert" \
    "curl -sf '$PROM/api/v1/alerts' | json \"'$alert' in [a['labels']['alertname'] for a in d['data']['alerts']]\" | grep -q True"
done

trace_services='lokiq "{service=~\".+\"} |= \"$TRACE_REQUEST_ID\"" | json "\",\".join(sorted({s[\"stream\"][\"service\"] for s in d[\"data\"][\"result\"]}))"'
wait_for "[[ \$($trace_services) == gateway,identity-workspace,notification,task ]]" 120 || true
check "request_id links gateway, identity-workspace, task and notification logs" \
  "[[ \$($trace_services) == gateway,identity-workspace,notification,task ]]"
check "event_id is searchable as structured metadata in task and notification logs" \
  "[[ \$(lokiq '{service=~\".+\"} | event_id=\"$TRACE_EVENT_ID\"' | json \"','.join(sorted({s['stream']['service'] for s in d['data']['result']}))\") == notification,task ]]"
check "level label is normalised" \
  "curl -sf '$LOKI/loki/api/v1/label/level/values' | json \"sorted(d['data'])\" | grep -q \"'error', 'info', 'warn'\""
check "bearer tokens and connection passwords never reach Loki" \
  "[[ \$(lokiq '{service=\"notification\"} |~ \"sample-secret-token-123|sample-broker-password\"' | json 'len(d[\"data\"][\"result\"])') == 0 ]]"
check "redacted probe line is stored" \
  "[[ \$(lokiq '{service=\"notification\"} |= \"redaction_probe\" |= \"[REDACTED]\"' | json 'len(d[\"data\"][\"result\"])') -gt 0 ]]"

auth=(-u "admin:$GRAFANA_ADMIN_PASSWORD")
for uid in prometheus loki; do
  check "Grafana data source healthy: $uid" \
    "curl -sf ${auth[*]} '$GRAFANA/api/datasources/uid/$uid/health' | json 'd[\"status\"]' | grep -q OK"
done
for uid in taskmgn-service-health taskmgn-event-pipeline taskmgn-trace; do
  check "dashboard provisioned: $uid" "curl -sf ${auth[*]} '$GRAFANA/api/dashboards/uid/$uid' >/dev/null"
done
check "Grafana refuses anonymous access" \
  "[[ \$(curl -s -o /dev/null -w '%{http_code}' '$GRAFANA/api/dashboards/uid/taskmgn-trace') == 401 ]]"

if (( failures > 0 )); then
  echo "$failures check(s) failed"
  "${compose[@]}" logs --no-color --tail=40 alloy prometheus || true
  exit 1
fi
echo "All monitoring checks passed with sample telemetry."
