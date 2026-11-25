# Notification Service

Issue #39 establishes the service runtime, database migration, private health
probes, telemetry, and test pipeline. Event delivery, listing, unread counts,
and mark-as-read are follow-up work.

The service uses Node.js 24 LTS to run JavaScript, TypeScript 5.9 to check
types at build time, Express 5 to handle HTTP, and PostgreSQL 17 or 18 to
store Notification data in a dedicated database. The built-in Node.js test
runner runs the test suites. npm installs the exact versions in the lockfile.

## Install and run locally

Run from `services/notification`:

```sh
npm ci
npm run build
npm start
```

In another terminal:

```sh
curl -i http://127.0.0.1:8002/health/live
```

Liveness returns HTTP 200 and `{"status":"ok"}` as soon as the process can
respond. Health responses include `X-Request-ID` and `Cache-Control: no-store`.

## Connect PostgreSQL and Identity

Provision a dedicated PostgreSQL database and role that can create objects in
that database. Copy `.env.example` to `.env` and set these values:

| Variable | Purpose |
| --- | --- |
| `HOST` | Listen address. Defaults to `127.0.0.1` locally. |
| `PORT` | Listen port. Defaults to `8002`. |
| `DATABASE_URL` | PostgreSQL connection URL for the service-owned database. |
| `IDENTITY_READY_URL` | Identity's private readiness URL, such as `http://127.0.0.1:8000/health/ready` when running on the host. |

With Identity running and the service built, run:

```sh
node --env-file=.env dist/migrate.js
node --env-file=.env dist/server.js
```

The migrator applies each `migrations/NNN_*.sql` file once, in order, in a
single transaction, and can be rerun. Readiness returns 200 and `{"status":"ok"}` when PostgreSQL can
query the migrated table and Identity returns 200. Unavailable or
unconfigured dependencies return 503 and `{"status":"unavailable"}`.

```sh
curl -i http://127.0.0.1:8002/health/ready
curl -i http://127.0.0.1:8002/metrics
```

The service forwards the correlation UUID to Identity. It replaces missing
or invalid IDs, caps request bodies at 64 KiB, and returns the shared JSON
error envelope for HTTP input failures. JSON request logs record the ID,
status, and duration using an allowlist of fields. The private Prometheus
endpoint exposes request and server error counters and cumulative request
duration in seconds. RabbitMQ exposes its own [Prometheus metrics](../../infra/broker/README.md).
Broker availability is outside the synchronous readiness check.

## Test

`npm test` builds the service and runs the unit and request suites without
external services. Use `npm run test:unit` or `npm run test:request` after a
build to run an individual suite.

For PostgreSQL integration tests, export `DATABASE_URL` for a disposable
database and run:

```sh
npm run build
npm run migrate
npm run migrate
npm run test:integration
```

The integration suite starts a controlled Identity health server, verifies
readiness and recovery, and checks recipient deduplication in PostgreSQL.
GitHub Actions runs all three suites before building and smoke-testing the
image.

## Process notification events

`processEvent(pool, message)` in `src/processing.ts` turns one broker message
into a stored notification. Broker consumption and acknowledgements are added
separately; until then, call it directly or through the tests.

It accepts `invitation.created.v1`, `task.assigned.v1` and
`task.comment_mentioned.v1`. Messages are validated against
`src/contracts/event.schema.json`, a copy of the shared event schema that a
unit test keeps in sync. The schema also fixes the producer for each type.

| Outcome | Result |
| --- | --- |
| New event | `created`: one unread notification for the recipient |
| Actor is the recipient | `suppressed`: marked processed, no notification |
| Event already processed | `duplicate`: no change |
| Malformed, oversized, unsupported or wrong producer | throws `InvalidEventError` before any write; do not retry |
| Database failure | throws; nothing is committed, so retry the same message |

The processed-event marker in `processed_events`, keyed by consumer name and
event ID, and the notification commit in one transaction. A replay after
commit, or concurrent copies of one event, therefore create one notification.
Notifications keep IDs and the actor's display name only. They are historical
records and do not grant access to the invitation, task or comment. Markers
and notifications are kept for the project lifetime.

To reproduce against a disposable database:

```sh
docker run -d --rm --name notification-test-db -p 127.0.0.1:55432:5432 \
  -e POSTGRES_DB=notification -e POSTGRES_USER=notification_app -e POSTGRES_PASSWORD=test-only postgres:17
export DATABASE_URL=postgresql://notification_app:test-only@127.0.0.1:55432/notification
npm run build && npm run migrate && npm run test:integration
docker exec notification-test-db psql -U notification_app -d notification \
  -c 'SELECT type, recipient_id, task_id, read_at FROM notification_service.notifications LIMIT 5'
docker stop notification-test-db
```

The integration tests cover every event type, replay, six concurrent
deliveries, self-suppression, rollback before commit and invalid events.

## Container

```sh
docker build -t notification:local .
docker run --rm -p 127.0.0.1:8002:8002 notification:local
```

Use the liveness curl command above to check the container. For readiness,
use a container network that can reach PostgreSQL and Identity and set the
URLs in the environment file to addresses reachable from that network.
Apply migrations with a one-shot container on that network:

```sh
docker run --rm --network notification-network --env-file .env notification:local node dist/migrate.js
docker run --rm --network notification-network --env-file .env -e HOST=0.0.0.0 -p 127.0.0.1:8002:8002 notification:local
```

Keep health and metrics routes on the private service network.

## Contracts

- [Notification OpenAPI contract](../../api_contracts/openapi/notification.openapi.json)
- [Private Identity OpenAPI contract](../../api_contracts/openapi/identity-internal.openapi.json)
- [Shared HTTP conventions](../../api_contracts/conventions.md)
- [Event schemas and delivery](../../api_contracts/events/README.md)
