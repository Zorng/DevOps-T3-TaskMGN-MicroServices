# Identity & Workspace Health Probes

The service implements private, unauthenticated `GET /health/live` and `GET /health/ready` operations from the [Identity OpenAPI contract](../../../api_contracts/openapi/identity-workspace.openapi.json). Keep these direct service routes outside the public gateway, as specified in [gateway routing](../../../api_contracts/gateway-routing.md#per-service-health-semantics).

## Responses

| Route | Condition | Response |
| --- | --- | --- |
| `/health/live` | The process can serve the request | 200 `{"status":"ok"}` |
| `/health/ready` | Migrated, writable database/outbox available | 200 `{"status":"ok"}` |
| `/health/ready` | Storage unavailable, incompatible, read-only, or missing required permissions | 503 `{"status":"unavailable"}` |

Bodies have exactly one `status` property. Every health response uses `Content-Type: application/json`, `Cache-Control: no-store`, and `X-Request-ID`. Canonical incoming UUIDs are preserved (uppercase is normalized to lowercase); missing/invalid IDs get a new UUIDv4. Dependency failures use the Health response above, not the general application error envelope, and never include database exceptions or credentials.

## Probe behavior

Liveness performs no database, broker, Google, or other network checks. It runs on the event loop without blocking database work, so it can respond while readiness waits for storage.

Readiness uses the existing SQLAlchemy pool in a worker thread. A fresh query verifies:

- Exactly one migration revision, matching `SCHEMA_REVISION` in `persistence.py` (currently `0004_membership_generations`).
- A read-write connection to the primary database, rather than recovery/read-only mode.
- The migrated outbox, authentication, workspace, membership, invitation, idempotency and cursor tables exist and the application role has SELECT, INSERT, and UPDATE privileges on them.

No rows are inserted, updated, or deleted by this probe. It performs no migration, login, business action, broker call, or event publication. Query/connection/pool waits use the bounded settings documented in [application startup](application-startup.md#configuration); these are separate stage limits, not a single overall HTTP deadline. Missing/unstarted resources, unsuccessful checks, and probe exceptions produce 503. Successful readiness is not cached between requests.

A broker outage alone does not fail readiness while this storage remains available. [Observability](observability.md) records probe results and exports their last status/time, without making database calls during metrics scrapes. Broker/outbox delivery metrics accompany the future publisher; no publisher is implemented by these routes. Update the expected schema revision alongside future migrations and their readiness tests.

## Try locally

Complete [database setup](database-setup.md), then start the app using the command in [application startup](application-startup.md#run-locally-with-powershell). In another PowerShell terminal:

```powershell
curl.exe -i http://127.0.0.1:8000/health/live
curl.exe -i http://127.0.0.1:8000/health/ready
```

Both should return 200 after the database migration. To check outage behavior on the dedicated local development database:

```powershell
docker compose stop db
curl.exe -i http://127.0.0.1:8000/health/live
curl.exe -i http://127.0.0.1:8000/health/ready
docker compose up --detach --wait --wait-timeout 60 db
curl.exe -i http://127.0.0.1:8000/health/ready
```

Run the Docker commands from `services/identity-workspace/`. During the outage, liveness remains 200 and readiness becomes 503; after recovery readiness returns to 200. Stopping this Compose database does not stop the separate Windows PostgreSQL installation.

## Verification

Use the separate unit/request suite commands in [application startup](application-startup.md#verification-commands) and the [PostgreSQL integration command](database-setup.md#integration-tests). All suites reject warnings and empty collections.

Health coverage includes application logic, both HTTP routes, exact OpenAPI response shapes and headers, request ID generation/propagation, safe dependency failures, and liveness responding while a readiness check is blocked. Tests with real disposable PostgreSQL cover absent/wrong migrations, missing outbox storage, read-only connections, revoked SELECT/INSERT/UPDATE permissions, statement timeout/recovery, connection pool exhaustion/recovery, and actual database stop/start recovery. The healthy probe also verifies that no outbox rows were created and runs without a broker. See the [test guide](testing.md) for the complete suite commands.

Step 8 adds [shared body limits, query validation, method/not-found error formatting, and global response headers](http-contract.md). Invalid health requests use that shared error envelope, while database unavailability continues to use the Health 503 body. The [CI guide](ci.md) documents the workflow; business API implementation and hosted CI verification remain pending.

## Results recorded for Step 7

- Unit suite: 60 passed; request suite: 19 passed; PostgreSQL integration suite: 24 passed, with zero skips.
- Both endpoints returned 200 with the contracted body, no-store header, and propagated request ID over real localhost HTTP using the configured development database.
- Disposable PostgreSQL stop/start testing returned readiness 503 during the outage and 200 after recovery; liveness remained 200.
- The temporary HTTP smoke-test server was shut down cleanly. The development database remains running separately.

Related documents: [contract checklist](contract-requirements.md), [database setup](database-setup.md), and [application startup](application-startup.md).
