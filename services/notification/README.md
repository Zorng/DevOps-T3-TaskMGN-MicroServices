# Notification Service

Issue #39 establishes the service runtime, database migration, private health
probes, telemetry, and test pipeline. Issue #40 adds session verification
for the notification routes. Event delivery, listing, unread counts, and
mark-as-read are follow-up work.

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

The transactional migration records its version and can be rerun. Its
unique event/recipient constraint prevents duplicate rows for the same
delivery. Readiness returns 200 and `{"status":"ok"}` when PostgreSQL can
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
The public notification routes are scheduled for #42.

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
