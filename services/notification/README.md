# Notification Service

Issue #39 establishes the service runtime, database migration, private health
probes, telemetry, and test pipeline. Issue #40 adds session verification,
#41 adds event processing and #42 adds the public notification API. Broker
consumption is follow-up work.

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
| `NOTIFICATION_CURSOR_SECRET` | At least 32 random bytes used to sign list cursors, for example from `openssl rand -base64 32`. |

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

## Verify Notification sessions

The Notification router passes each protected request through Identity's
private `POST /internal/v1/auth/introspect` endpoint before the handler can
access recipient data. When adding routes, mount them through the guarded
router in `createApp` and use `response.locals.recipientId` to scope every
storage operation. The guard gets that ID from Identity on every request.

Set `IDENTITY_BASE_URL` to Identity's private HTTP(S) origin (no path) and
`NOTIFICATION_IDENTITY_SERVICE_KEY` to the same dedicated secret configured
as `IDENTITY_NOTIFICATION_SERVICE_KEY` on Identity. Keep the secret in
your ignored `.env` or deployment secret configuration. Use the private
network and TLS when calling across hosts.

A missing bearer returns 401 `AUTH_REQUIRED`. A revoked or expired session
returns 401 `SESSION_INVALID`. Rejected service credentials, timeouts and
other dependency failures return 503 `DEPENDENCY_UNAVAILABLE` with
`Retry-After: 1`. The private POST has a two-second timeout per attempt
and retries once on connection failure or HTTP 503. The unit and HTTP tests
use controlled Identity responses and run in the existing CI workflow.

### Check against real Identity

Run from this directory with Identity running, a configured `.env`, and an
application bearer obtained from your usual sign-in flow. Build the service
using `npm run build`. In bash or zsh, define this probe:

```sh
probe_identity() {
  printf 'Paste the application token (input hidden): '
  read -r -s PROBE_TOKEN
  printf '\n'
  PROBE_TOKEN="$PROBE_TOKEN" node --env-file=.env - <<'NODE'
const { randomUUID } = require('node:crypto');
const { createIdentityClient } = require('./dist/identity.js');
const introspect = createIdentityClient(
  process.env.IDENTITY_BASE_URL,
  process.env.NOTIFICATION_IDENTITY_SERVICE_KEY,
);
introspect('Bearer ' + process.env.PROBE_TOKEN, randomUUID())
  .then(({ kind }) => console.log(kind));
NODE
  unset PROBE_TOKEN
}
```

1. Run `probe_identity` with a valid token. Expect `verified`.
2. Log out of that same session through the application, then run the probe
   with the same token. Expect `invalid`.
3. Sign in again, then run
   `NOTIFICATION_IDENTITY_SERVICE_KEY=deliberately-invalid-demo-key probe_identity`
   with the new valid token. Expect `credential_rejected`. This overrides
   the key for the probe without changing `.env`.

The probe prints only the outcome. Request tests verify how those outcomes
map to public HTTP responses through the guard.

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

## Notification API

All three routes run behind the session guard and use only the recipient ID
that Identity returns. There is no public create endpoint; notifications come
from events.

| Route | Result |
| --- | --- |
| `GET /api/v1/notifications` | Page of the caller's notifications, newest first (`created_at`, then `id`, descending). Accepts `limit` (1-100, default 20), `cursor` and `unread_only` (`true` or `false`). |
| `GET /api/v1/notifications/unread-count` | `{"count": n}`, counted at read time |
| `POST /api/v1/notifications/{notification_id}/read` | Sets `read_at` on the first call and returns the notification. Repeats keep the first `read_at`. |

- Any other query parameter, a repeated parameter or a request body returns
  400 `MALFORMED_REQUEST`. A non-JSON body returns 415.
- `next_cursor` is an HMAC-signed token bound to the recipient and the
  `unread_only` value. Using it for another recipient or filter, or changing
  it, returns 400 `INVALID_CURSOR`. The last page has `next_cursor: null`.
- Another recipient's notification, or one that does not exist, returns 404
  `NOT_FOUND`.
- Without a database or a cursor secret of at least 32 bytes, the routes
  return 503 `DEPENDENCY_UNAVAILABLE` with `Retry-After: 1`.
- Notifications are historical. Their IDs grant no access to the invitation,
  task or comment; the owning service checks access when they are opened.

Migration 003 stores `created_at` and `read_at` at millisecond precision so
API timestamps and cursor positions match exactly, and adds indexes for the
recipient list and unread count.

Example, with the service running and a valid application token:

```sh
TOKEN='paste-application-token'
curl -s -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8002/api/v1/notifications?limit=2&unread_only=true'
curl -s -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8002/api/v1/notifications?limit=2&cursor=NEXT_CURSOR'
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8002/api/v1/notifications/unread-count
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8002/api/v1/notifications/NOTIFICATION_ID/read
```

Until broker consumption exists, seed an assignment notification for the
signed-in user (their ID is `user.id` from `GET /api/v1/me` on Identity):

```sh
RECIPIENT_ID='your-user-id' node --env-file=.env -e "
const { randomUUID } = require('node:crypto');
const [, event] = require('../../api_contracts/examples/events.json');
const { createPool } = require('./dist/dependencies.js');
const { processEvent } = require('./dist/processing.js');
const pool = createPool(process.env.DATABASE_URL);
event.id = randomUUID();
event.data.recipient_id = process.env.RECIPIENT_ID;
processEvent(pool, JSON.stringify(event)).then(console.log).finally(() => pool.end());
"
```

Request tests validate every response against the Notification OpenAPI
schemas and cover authentication, recipient isolation and validation for each
route. Integration tests cover paging with tied timestamps, the unread
filter, exact counts and concurrent mark-as-read against PostgreSQL.

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
