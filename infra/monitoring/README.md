# Monitoring

Status: collection, alert and dashboard configuration in place and verified with sample telemetry (issue #44). Deployment next to the live stack and live verification belong to #34. The technical explanation and the evidence are in [docs/observability.md](../../docs/observability.md).

| Component | Version | Role |
| --- | --- | --- |
| Prometheus | `prom/prometheus:v3.16.0` | Scrapes every service's metrics endpoint and evaluates alert rules |
| Loki | `grafana/loki:3.7.9` | Stores container logs for 7 days |
| Alloy | `grafana/alloy:v1.21.3` | Reads container logs from Docker, redacts secrets, adds labels, sends them to Loki |
| Grafana | `grafana/grafana:13.3.1` | Dashboards and log search |

## Access

Everything stays on the operational path: Grafana (`3000`), Prometheus (`9090`) and Loki (`3100`) bind to `127.0.0.1` only, and nothing is routed through the public gateway. Grafana needs `GRAFANA_ADMIN_PASSWORD`, has no anonymous access and no sign-up. On a server, reach it through an SSH tunnel, for example `ssh -L 3000:127.0.0.1:3000 <host>`. Change the ports with `GRAFANA_PORT`, `PROMETHEUS_PORT` and `LOKI_PORT`.

## Run

From the repository root:

```sh
# Configuration check with sample telemetry (starts, checks, removes everything)
infra/monitoring/scripts/verify.sh

# Keep the sample stack running to look around, then open http://127.0.0.1:3000
KEEP=1 GRAFANA_ADMIN_PASSWORD='choose-one' infra/monitoring/scripts/verify.sh
docker compose -p taskmgn-monitoring-verify -f infra/monitoring/compose.yaml -f infra/monitoring/compose.sample.yaml down -v

# Watch the real local stack from infra/compose.yaml (start it first)
GRAFANA_ADMIN_PASSWORD='choose-one' docker compose -f infra/monitoring/compose.yaml -f infra/monitoring/compose.stack.yaml up -d --wait
```

The sample containers use the real service names, metric names and log formats, so the configuration loads unchanged. Each sample log line has `"sample": true` and the label `stack="sample"`. Never present sample data as live evidence.

## Files

| Path | Purpose |
| --- | --- |
| `prometheus/prometheus.yml` | Scrape targets, one job per component, each with a `service` label |
| `prometheus/alerts.yml` | Alert rules |
| `prometheus/alerts.test.yml` | `promtool test rules` cases for the alerts |
| `loki/loki.yaml` | Single-node Loki with 7-day retention |
| `alloy/config.alloy` | Docker log discovery, redaction, labels and structured metadata |
| `grafana/provisioning/` | Data sources (`prometheus`, `loki`) and the dashboard folder |
| `grafana/dashboards/` | Service health, Event pipeline and Trace an ID |
| `compose.yaml` | The four monitoring containers |
| `compose.sample.yaml` | Sample telemetry under the real service names |
| `compose.stack.yaml` | Joins Prometheus to the `taskmgn_backend` and `taskmgn_messaging` networks |
| `sample/telemetry.py` | Sample metrics and logs |
| `scripts/verify.sh` | Loads the stack with sample telemetry and checks it |

## Scrape targets

| Job / `service` | Target | Owner |
| --- | --- | --- |
| `identity-workspace` | `identity-workspace:8000/metrics` | Identity |
| `identity-outbox` | `identity-outbox:8001/metrics` | Identity |
| `task` | `task:8001/actuator/prometheus` | Task |
| `notification` | `notification:8002/metrics` | Notification |
| `broker` | `broker:15692/metrics` (per-queue metrics are enabled) | Infrastructure |

The gateway has no metrics endpoint yet, so its request rate, latency and upstream errors come from its JSON access log in Loki.

## Logs

Alloy collects logs from containers in the Compose projects matching `MONITORED_PROJECTS` (default `taskmgn`) and from containers labelled `taskmgn.logs=true`. Labels are `service` (the Compose service name), `stack` and `level` (`info`, `warn` or `error`). `request_id` and `event_id` are stored as structured metadata, not labels, because every value is unique.

Before storage Alloy replaces bearer tokens, the password part of `amqp://`, `amqps://` and `postgres://` URLs, and values of `password`, `secret`, `token` and `service_key` fields with `[REDACTED]`. Services already exclude these; this is a second safety net.

Useful queries in Grafana Explore (Loki):

```logql
{service=~".+"} |= "6b3f2c1e-8a4d-4f5b-9c7e-1d2a3b4c5d6e"        # every line with this request or event ID
{service=~".+"} | event_id="9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"  # exact event_id match
{level="error"}                                                    # all errors
{service="notification"} |= "event_dead_lettered"                  # dead-lettered events
{service="gateway"} | json | status >= 500                         # failed public requests
```

The line filter (`|=`) also matches Task, which writes IDs inside its message text (`event_id=...`).

## Alerts

| Alert | Fires when | Severity |
| --- | --- | --- |
| `ServiceDown` | A metrics endpoint is unreachable for 2 minutes | critical |
| `IdentityNotReady` | Identity readiness reports its database down for 2 minutes | critical |
| `HighServerErrorRate` | More than 5% of a service's requests return 5xx for 5 minutes | warning |
| `OutboxBacklogOld` | An outbox event has been pending for more than 5 minutes | critical |
| `OutboxPublishFailing` | Publish failures keep occurring for 5 minutes | warning |
| `DeadLettersWaiting` | Any `.dlq` queue holds a message for 1 minute | warning |
| `ConsumerFailuresPersisting` | More than 5 processing failures in 10 minutes | warning |
| `CleanupLagHigh` | More than 50 cleanup events waiting for 10 minutes | warning |

Alerts appear in Prometheus (`/alerts`). Routing them to email or chat needs an Alertmanager and a destination, which are not chosen yet.
