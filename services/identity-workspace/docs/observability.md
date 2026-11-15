# Identity & Workspace Logs and Metrics

The service now emits structured application logs and exposes private Prometheus metrics. It uses Python's standard logging library and the already-locked Prometheus client; no additional packages or monitoring server are needed to verify this foundation.

## Structured logs

Application events are JSON lines on stderr. Request completion records include a UTC timestamp with millisecond precision, service name, level, event, request ID, HTTP method, registered route template, status code, duration in milliseconds, and a controlled error category when applicable.

```json
{"timestamp":"2026-11-15T08:00:00.000Z","service":"identity-workspace","level":"ERROR","event":"http_request_completed","request_id":"00000000-0000-4000-8000-000000000012","method":"GET","route":"/health/ready","status_code":503,"duration_ms":12.5,"error_category":"dependency_unavailable"}
```

The request ID matches X-Request-ID in the response. Readiness checks also emit a correlated `readiness_checked` event with `ready`/`unavailable` and a safe `storage_unavailable` category on failure. Lifecycle events report startup/shutdown; they have a null request ID because no HTTP request is involved. Lifecycle resource failures propagate constant, credential-safe errors so server-managed tracebacks do not reveal raw driver messages.

`IDENTITY_LOG_LEVEL` controls application logging per app instance. INFO includes successful requests and lifecycle events; WARNING includes request rejections; ERROR includes server/dependency failures. Changing this level does not change third-party/root logger settings or disable metrics.

Keep `--no-access-log` in the [documented Uvicorn command](application-startup.md#run-locally-with-powershell). The service supplies its own safe request logs. Uvicorn's framework startup messages have separate CLI-controlled logging; application JSON formatting does not rewrite those messages.

## Credential-safe diagnostics

Logging uses a closed allowlist, not general object/string serialization. It excludes headers, cookies, request/response bodies, database URLs/passwords, Google/app tokens, service keys, email lookup values, query strings, path parameter values, raw exception messages, and tracebacks. Unknown event names/extras are discarded; invalid methods become OTHER. Routes must match a registered template, such as `/workspaces/{workspace_id}`, rather than an incoming path.

Unknown paths and requests rejected before route selection use `unmatched`. This avoids leaking user content or creating a distinct label for every guessed URL. These rules still apply at DEBUG. Future service code should use `app.state.observability.log.event(...)` with controlled event/field values; it must not introduce free-form credential logging or enable raw HTTP/database debug logging.

A log sink failure does not dump the original record through logging's fallback or stop request processing. It increments `identity_log_write_failures_total`, allowing the failure to be observed through metrics.

## Private metrics endpoint

`GET /metrics` returns Prometheus text, with the shared X-Request-ID and Cache-Control: no-store headers. It requires no application session and must remain outside public gateway routing, like the health probes. POST/unknown query parameters are rejected through the shared HTTP error handlers. Private preflight paths remain unavailable through the public CORS preflight policy.

Scraping performs no database, broker, Google, or other network calls. Each app has its own CollectorRegistry, so independently created apps do not share counts or duplicate registrations.

| Metric | Meaning |
| --- | --- |
| `identity_http_requests_total` | Requests by bounded method, route template, and status code |
| `identity_http_request_duration_seconds` | Request duration histogram by method/route |
| `identity_http_errors_total` | Client/server error counts |
| `identity_readiness_status` | Last completed probe: -1 unknown, 0 unavailable, 1 ready |
| `identity_readiness_last_check_timestamp_seconds` | Last completed probe's Unix timestamp; 0 before any probe |
| `identity_readiness_checks_total` | Probe counts by ready/unavailable result |
| `identity_readiness_duration_seconds` | Probe duration histogram by result |
| `identity_log_write_failures_total` | Structured log formatting/output failures |
| `identity_service_info` | Installed service package version |

Request IDs, users, resource IDs, emails, raw URLs, and exception text are never metric labels. Method values are a fixed HTTP-method set plus OTHER. Disconnects before a response use an internal 499 observation; the service does not send an HTTP 499 response. Counters/latency/log records are updated at request completion, so a metrics response includes previous completed scrapes rather than its own not-yet-completed request.

The readiness gauge is an observation, not a background database monitor. Only a completed `/health/ready` request updates it. Check its timestamp when interpreting an old value; merely scraping `/metrics` does not refresh database health. A failed readiness check sets 0 while the exporter remains available. A recovered check sets 1.

This foundation targets one Uvicorn worker per container. Metrics are per app/worker; multiple workers would require a suitable multiprocess collection design, while separate replicas can be scraped as separate targets. Centralized log shipping, dashboards, host/process monitoring, broker metrics, and outbox publication/retry metrics accompany the corresponding infrastructure/publisher work.

## Try locally

Start the service using [application startup](application-startup.md). In another terminal:

```powershell
curl.exe -i http://127.0.0.1:8000/health/ready
curl.exe -i http://127.0.0.1:8000/missing
curl.exe http://127.0.0.1:8000/metrics
```

Find the response's X-Request-ID in the JSON request log to correlate the operation. A missing route returns 404 and produces a WARNING request event. The readiness call updates the database observation before the scrape.

To demonstrate a database outage, use the commands in [health probes](health-probes.md#try-locally), request readiness during the outage, and fetch `/metrics`. The probe returns 503, the gauge becomes 0, and metrics still return 200. Restore the database and request readiness again to observe recovery. These Docker commands target only the dedicated Identity development database.

To inspect DEBUG output, set `IDENTITY_LOG_LEVEL=DEBUG` in the ignored local `.env` (or shell) and restart the app. Credential exclusion remains enabled. Keep real secrets and any locally redirected logs outside commits.

## Verification recorded for Step 9

- Unit suite: 139 passed; request suite: 80 passed; PostgreSQL integration suite: 24 passed, with zero skips.
- Tests verify JSON/UTC formatting, response/log request correlation, per-instance levels/registries, credential exclusion at DEBUG, bounded labels, request counts/latency/errors, unknown/failing/recovered readiness, and metrics text/content type.
- Startup/shutdown exceptions and log-output failures were tested without leaking synthetic credentials. Metrics remain available when a log sink fails.
- Real PostgreSQL tests verify that metrics scraping succeeds during database stop/start, schema/permission failures, and recovery.
- A real localhost HTTP scrape parsed as Prometheus text and showed readiness/request metrics. Correlated application JSON logs at DEBUG excluded synthetic credentials, email queries, and raw missing paths; the temporary server stopped cleanly afterward.

Run the separate suite commands in [application startup](application-startup.md#verification-commands) and [database setup](database-setup.md#integration-tests). The original README is retained. The [container guide](container.md) and [CI guide](ci.md) cover subsequent implementation and verification; a hosted CI result remains pending.

Related documents: [HTTP handling](http-contract.md), [health probes](health-probes.md), [application startup](application-startup.md), and [contract checklist](contract-requirements.md).
