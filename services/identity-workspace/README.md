# Identity & Workspace Service

Status: FastAPI foundation, Google sign-in/application sessions, workspace management, membership permissions, registered-user invitations, private identity/access endpoints and reliable lifecycle publication are implemented. A separate outbox worker delivers committed events with confirms, routing acceptance and durable retries. RabbitMQ is an opt-in reference/test adapter; Infrastructure owns shared broker deployment. Notification and Task consumers are not available yet, so their final integration demonstration remains open. Public-gateway isolation also awaits the gateway implementation; see [private API setup and deployment verification](docs/private-access-apis.md).

Runtime: Python 3.13.16, FastAPI 0.163.0, PostgreSQL 18.7, SQLAlchemy 2, Psycopg 3, Alembic, and uv 0.13.8. Exact dependencies are locked in [uv.lock](uv.lock); see [stack decisions](docs/stack-decisions.md).

## Setup and local verification

Run these commands from this directory after [installing the selected Python and uv versions](docs/environment-setup.md):

```powershell
$identityUv = Join-Path $PWD '.tools/uv/bin/uv.exe'
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
& $identityUv sync --locked
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $identityUv run --locked python scripts/setup_local_db.py
if ($LASTEXITCODE -ne 0) { throw "Local configuration setup failed." }
```

The setup script preserves existing configuration and generates ignored database password files, `.env` for host Python, and `.env.container` for Docker. Configure the complete public Google OAuth web client ID as `IDENTITY_GOOGLE_CLIENT_ID` in the environment file for your chosen runtime, plus explicit `IDENTITY_ALLOWED_ORIGINS`. Real Google provisioning is separate from automated tests. This ID-token popup flow needs no Google client secret in the service.

For example, set these locally using your actual client ID:

```dotenv
IDENTITY_GOOGLE_CLIENT_ID=REPLACE_WITH_WEB_CLIENT_ID.apps.googleusercontent.com
IDENTITY_ALLOWED_ORIGINS='["http://localhost:5173"]'
```

Start Docker Desktop, then build and start the service/database. Compose runs Alembic migrations once before application startup:

```powershell
docker compose --profile app build identity
if ($LASTEXITCODE -ne 0) { throw "Image build failed." }
docker compose --profile app up --detach --no-build --wait --wait-timeout 90 identity
if ($LASTEXITCODE -ne 0) { throw "Container startup failed." }
curl.exe -i http://127.0.0.1:8000/health/ready
```

Expected readiness: 200 after migration `0005_registered_invitations`. With no client ID configured, health can still run, while Google challenge/exchange return 503. Configure a real web client ID to perform browser sign-in. Rebuild the image and rerun the startup command when upgrading an older local container; migrations run before the new application starts.

Follow [Google configuration, browser verification, and secret-safe troubleshooting](docs/google-signin.md). A local [browser verification tool](tools/google-signin.html) exercises challenge, exchange, current user, and logout while retaining tokens only in page memory. For host Python startup, see [application startup](docs/application-startup.md); for database ownership/migrations, see [database setup](docs/database-setup.md).

## Authentication endpoints

| Method/path | Behavior |
| --- | --- |
| `POST /api/v1/auth/google/challenges` | Allowed Origin required; creates a five-minute single-use nonce |
| `POST /api/v1/auth/google/exchange` | Verifies Google identity/nonce and returns an eight-hour opaque app session |
| `POST /api/v1/auth/logout` | Revokes only the presented application session; 204, then 401 on replay |
| `GET /api/v1/me` | Fresh session validation; returns current user and stored owned-workspace count |

Users are identified by Google subject, never merged by email. Only session token hashes are stored. No application session cookie or refresh endpoint is introduced. Unauthenticated health and metrics routes remain private operational endpoints.

## Workspace endpoints

All five operations require an unexpired application bearer session.

| Method/path | Behavior |
| --- | --- |
| `GET /api/v1/workspaces` | Active memberships only; `limit` defaults to 20 (maximum 100), opaque keyset cursor |
| `POST /api/v1/workspaces` | JSON `name`, UUID `Idempotency-Key`; 201 with owner membership and `ETag: "1"`; ten active owned workspaces maximum |
| `GET /api/v1/workspaces/{workspace_id}` | Current member only; 200 with ETag, inaccessible/deleted returns 404 |
| `PATCH /api/v1/workspaces/{workspace_id}` | Owner-only rename; JSON `name` and exact `If-Match` required |
| `DELETE /api/v1/workspaces/{workspace_id}` | Owner-only authoritative deletion with `If-Match`; 204 after atomic local commit |

Names are trimmed and checked at 1–100 Unicode code points. Missing/stale preconditions return 428/412; current members attempting owner-only mutations receive 403. Joined workspaces do not consume owned quota. Successful create responses and ETags are retained for 25 hours; retry an uncertain create with the same key and normalized body. See [reproducible HTTP examples, races, persistence, and deletion handoff](docs/workspace-management.md).

## Membership endpoints

All operations require a live application session and current workspace membership. A revoked member or outsider gets 404. Revoking workspace membership leaves the user's application session valid for other workspaces.

| Method/path | Behavior |
| --- | --- |
| `GET /api/v1/workspaces/{workspace_id}/members` | At most 20 active members, ordered `joined_at ASC, membership_id ASC`; optional `q` searches display names case-insensitively (0–100 Unicode code points) |
| `DELETE /api/v1/workspaces/{workspace_id}/members/{user_id}` | Owner removes a non-owner; 204, including an already absent target while the caller remains owner |
| `DELETE /api/v1/workspaces/{workspace_id}/membership` | Current non-owner leaves; 204, then 404 on repeat |

Current non-owners attempting removal get 403. Owners cannot leave or be removed (409 `OWNER_CANNOT_LEAVE`). Member responses contain user ID/display name, role, immutable membership ID and join time; emails and provider identifiers are excluded. Removal/leave need no `If-Match` or idempotency key. A real revocation increments workspace version, preserves the old generation and atomically saves `workspace.member_removed.v1` with reason `removed` or `left`. Every rejoin creates a new generation. See [setup, examples, shared primitives and event integration](docs/membership-permissions.md).

## Invitation endpoints

Owners invite existing registered users by trimmed case-insensitive email. There is no separate user-directory endpoint. Pending invitations neither reserve capacity nor expire; acceptance enforces twenty active members including the owner.

| Method/path | Behavior |
| --- | --- |
| `POST /api/v1/workspaces/{workspace_id}/invitations` | Current owner only; JSON `email` and UUID `Idempotency-Key`; 201 with invitation and atomic creation event |
| `GET /api/v1/workspaces/{workspace_id}/invitations` | Current owner lists workspace invitation history |
| `GET /api/v1/invitations` | Recipient lists their invitations from active workspaces |
| `GET /api/v1/invitations/{invitation_id}` | Recipient or current owner reads an invitation |
| `POST /api/v1/invitations/{invitation_id}/response` | Recipient accepts/declines via JSON `action`; 200, with state-based terminal retries |
| `POST /api/v1/invitations/{invitation_id}/cancel` | Current owner cancels; bodyless, 200, with repeat cancellation allowed |

Both lists support `status`, `limit` (default 20, maximum 100) and an authenticated opaque cursor, ordered `created_at DESC, id DESC`. Invitation responses contain ID/display-name summaries, never email addresses. No ETag/If-Match is used. Repeating an accepted invitation after leaving returns its accepted record and never rejoins the user; a new invitation is required. See [lookup errors, visibility/transition rules, examples, migration and verification](docs/registered-user-invitations.md).

## Private Identity APIs

These are service-to-service POSTs with no body/query. Both require the original application bearer and a distinct per-service `X-Service-Key`. Task may call both; Notification may introspect only. Responses are never cached, and every authorized call checks current database state. Identity assertion headers do not replace authentication.

| Method/path | Task | Notification |
| --- | --- | --- |
| `POST /internal/v1/auth/introspect` | Live user ID/display name and session expiry | Same |
| `POST /internal/v1/workspaces/{workspace_id}/access` | Active workspace version, actor role/generation and at most twenty current members | 403 |

To enable local private calls, provision ignored keys and start with the optional secret-file overlay:

```powershell
& $identityUv run --locked python scripts/setup_service_keys.py
if ($LASTEXITCODE -ne 0) { throw "Service-key setup failed." }
docker compose -f compose.yaml -f compose.private.yaml --profile app build identity
if ($LASTEXITCODE -ne 0) { throw "Image build failed." }
docker compose -f compose.yaml -f compose.private.yaml --profile app up --detach --no-build --wait --wait-timeout 90 identity
if ($LASTEXITCODE -ne 0) { throw "Private API startup failed." }
```

No new dependencies or migration are needed; head remains `0005_registered_invitations`. Existing public-only setup still works with private callers disabled until their keys are configured. See [secret configuration/rotation, consumer fixtures and deployment verification](docs/private-access-apis.md). **The gateway does not exist yet, so public-route isolation is an open acceptance check.** Local private API tests do not prove deployed gateway routing.

## Lifecycle publisher setup

From this directory, after the database/service setup above:

```powershell
& $identityUv run --locked python scripts/setup_broker.py
if ($LASTEXITCODE -ne 0) { throw "Broker configuration failed." }
docker compose -f compose.yaml -f compose.outbox.yaml --profile app --profile outbox build identity outbox
if ($LASTEXITCODE -ne 0) { throw "Image build failed." }
docker compose -f compose.yaml -f compose.outbox.yaml --profile app --profile outbox up --detach --no-build --wait --wait-timeout 120 identity outbox
if ($LASTEXITCODE -ne 0) { throw "Publisher startup failed." }
```

This generates ignored local broker secrets, provisions durable logical queues and starts a separate worker using the same non-root service image. Keep `-f compose.private.yaml` in the commands too if you already use private service keys. Broker failure leaves committed operations successful and events pending; readiness continues to check writable PostgreSQL. Migration head remains `0005_registered_invitations`; existing lease/retry columns are sufficient, so no migration is needed.

See [broker configuration, retry/recovery, metrics and reproducible checks](docs/lifecycle-outbox.md), the [technical report](../../docs/identity-lifecycle-outbox-report.md), and the [demo/consumer handoff](../../docs/identity-lifecycle-outbox-demo.md). CI uses generated test credentials, Google doubles and disposable PostgreSQL/RabbitMQ; no production secrets are required.

## Test and CI commands

```powershell
& $identityUv run --locked pytest tests/unit -q --junitxml=test-results/unit.xml
if ($LASTEXITCODE -ne 0) { throw "Unit tests failed." }
& $identityUv run --locked pytest tests/request -q --junitxml=test-results/request.xml
if ($LASTEXITCODE -ne 0) { throw "Request tests failed." }
& $identityUv run --locked pytest tests/integration -q --junitxml=test-results/integration.xml
if ($LASTEXITCODE -ne 0) { throw "Integration tests failed." }
& $identityUv run --locked python scripts/smoke_container.py
if ($LASTEXITCODE -ne 0) { throw "Container smoke test failed." }
```

Unit/request tests use controlled collaborators; integration tests use real disposable PostgreSQL/RabbitMQ and Google verification doubles. Producer checks include unroutable messages, broker restart/outage recovery, lease fencing and duplicate redelivery. Provider-adapter tests verify real RSA signatures with ephemeral test keys and a mocked key transport. Pytest blocks real requests HTTP calls, so no Google credentials or live Google connection enter CI. All suites must be nonempty and successful, with no skipped tests. See [test details](docs/testing.md) and [CI gates/report artifacts](docs/ci.md).

The [container guide](docs/container.md) documents build/run/stop commands and smoke checks. Expired/revoked authentication rows can be removed in bounded batches using `python -m identity_workspace.auth.cleanup --limit 1000` in the configured runtime; [authentication maintenance](docs/google-signin.md#maintenance) documents the Docker/uv commands.

## Scope

Google sign-in and application sessions; users, workspaces, memberships, roles, invitations, workspace/member limits.

Also implement the private [identity access contract](../../api_contracts/openapi/identity-internal.openapi.json). Publish invitation, member-removal, and workspace-deletion events through a transactional outbox.

## Contracts

- [Public REST contract](../../api_contracts/openapi/identity-workspace.openapi.json)
- [Authentication](../../api_contracts/authentication.md)
- [Cross-service workflows](../../api_contracts/workflows.md)
- [Event schemas and delivery](../../api_contracts/events/README.md)
- [Acceptance scenarios](../../api_contracts/acceptance-scenarios.md)

## Implementation layout

Keep this service's source, tests, dependency manifest/lockfile, database migrations, Dockerfile, and environment example in this directory. The internal layout can follow the selected framework's conventions. This directory is the service's container build context.

The service owns its database and accesses other services through contracts. Implement `/health/live` and `/health/ready`, structured logs, metrics, and the required publication/consumption reliability behavior. Build ARM64-compatible images if deployed on Oracle ARM instances.

The setup commands above use the selected runtime. Detailed subject-specific instructions remain in `docs/` to keep the service overview readable.

## Required tests and CI

Every implemented endpoint needs dedicated unit tests and request-level HTTP tests covering successful behavior and applicable failure cases. Include private routes/health probes and event-processing tests where applicable. Mock external collaborators in unit tests; verify persistence and transaction behavior separately with a disposable test database.

The [Identity workflow](../../.github/workflows/identity-workspace.yml) runs the separate suites and gates image build/smoke verification and main-branch publishing. Test failures, empty suites, and skipped tests fail the checks. Follow the shared [testing and CI policy](../../CONTRIBUTING.md#endpoint-tests-and-ci-requirements).
