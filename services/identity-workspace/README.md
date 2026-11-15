# Identity & Workspace Service

Status: FastAPI foundation and Google sign-in/application sessions are implemented. Workspace, membership, invitation, and private authorization APIs remain follow-up tasks.

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

Expected readiness: 200 after migration `0002_google_sessions`. With no client ID configured, health can still run, while Google challenge/exchange return 503. Configure a real web client ID to perform browser sign-in.

Follow [Google configuration, browser verification, and secret-safe troubleshooting](docs/google-signin.md). A local [browser verification tool](tools/google-signin.html) exercises challenge, exchange, current user, and logout while retaining tokens only in page memory. For host Python startup, see [application startup](docs/application-startup.md); for database ownership/migrations, see [database setup](docs/database-setup.md).

## Authentication endpoints

| Method/path | Behavior |
| --- | --- |
| `POST /api/v1/auth/google/challenges` | Allowed Origin required; creates a five-minute single-use nonce |
| `POST /api/v1/auth/google/exchange` | Verifies Google identity/nonce and returns an eight-hour opaque app session |
| `POST /api/v1/auth/logout` | Revokes only the presented application session; 204, then 401 on replay |
| `GET /api/v1/me` | Fresh session validation; returns current user and stored owned-workspace count |

Users are identified by Google subject, never merged by email. Only session token hashes are stored. No application session cookie or refresh endpoint is introduced. Unauthenticated health and metrics routes remain private operational endpoints. Protected workspace/private service operations are subsequent tasks.

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

Unit/request tests use controlled collaborators; integration tests use real disposable PostgreSQL and Google verification doubles. Provider-adapter tests verify real RSA signatures with ephemeral test keys and a mocked key transport. Pytest blocks real requests HTTP calls, so no Google credentials or live Google connection enter CI. All suites must be nonempty and successful, with no skipped tests. See [test details](docs/testing.md) and [CI gates/report artifacts](docs/ci.md).

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
