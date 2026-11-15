# Identity & Workspace Configuration and Startup

The service has a FastAPI application factory, validated environment configuration, lazy SQLAlchemy resources, [health endpoints](health-probes.md), [shared HTTP handling](http-contract.md), and [structured logs/private metrics](observability.md). [Database setup](database-setup.md) provides PostgreSQL and migrations. The [container guide](container.md) provides Docker startup and smoke verification, and the [CI guide](ci.md) documents the implemented workflow and pending hosted verification. Business APIs remain subsequent work.

## Configuration

Run commands from `services/identity-workspace/`. Settings load from the current directory's `.env` file and `IDENTITY_` environment variables; environment variables override the file. No settings or database resources are created merely by importing the package.

| Variable | Default | Meaning |
| --- | --- | --- |
| `IDENTITY_ENVIRONMENT` | `development` | `development`, `test`, or `production` |
| `IDENTITY_LOG_LEVEL` | `INFO` | Application JSON logging threshold: DEBUG, INFO, WARNING, ERROR, or CRITICAL |
| `IDENTITY_ALLOWED_ORIGINS` | `[]` | JSON list of explicit frontend origins; empty means requests with Origin are denied |
| `IDENTITY_DATABASE_URL` | Required | `postgresql+psycopg://user:password@host:port/database` |
| `IDENTITY_DB_CONNECT_TIMEOUT_SECONDS` | `2` | Driver connection timeout, integer from 2 to 30 seconds |
| `IDENTITY_DB_POOL_TIMEOUT_SECONDS` | `2` | Maximum pool checkout wait, greater than 0 and at most 30 seconds |
| `IDENTITY_DB_STATEMENT_TIMEOUT_MS` | `2000` | PostgreSQL statement timeout, integer from 1 to 30,000 milliseconds |
| `IDENTITY_DB_POOL_SIZE` | `5` | Connections retained per process, integer from 1 to 20 |
| `IDENTITY_DB_MAX_OVERFLOW` | `0` | Additional connections per process, integer from 0 to 20 |

Connection URLs must contain a host, database, username, and password. Percent-encode reserved characters in passwords. Supported URL query parameters are the PostgreSQL TLS settings `sslmode`, `sslrootcert`, `sslcert`, and `sslkey`; repeated or other parameters are rejected. Configure timeouts through their dedicated settings so a URL cannot override them. The service identifies its connections as `identity-workspace`.

Malformed/missing configuration fails startup with a `ConfigurationError` listing affected known setting names and omitting their values. The database URL is a secret field, excluded from settings representations and masked in serialized settings. `load_settings()` suppresses underlying parsing exceptions from the rendered traceback. Avoid printing raw validation errors or explicitly extracting/logging secret values in future code.

Unknown entries in `.env` are rejected to catch configuration mistakes. Use a service-specific file; leave unrelated service/database administration variables outside it. Only the settings above are needed at this stage.

## Run locally with PowerShell

First follow [environment setup](environment-setup.md) and [database setup](database-setup.md), which generates the ignored `.env` for Compose. For a manually configured database, copy the example once, preserving any existing local configuration:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

For manual setup, edit `.env` locally and replace the password placeholder with credentials for your dedicated Identity database. The example targets the Compose database on localhost port 5434; use port 5432 if connecting to your native installation. The database setup script generates local credentials only into ignored files; `.env` remains ignored by Git.

Start the application:

```powershell
$identityUv = Join-Path $PWD '.tools/uv/bin/uv.exe'
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
& $identityUv run --locked uvicorn identity_workspace.main:create_app --factory --host 127.0.0.1 --port 8000 --log-level info --no-access-log
```

If uv is installed on PATH, use `uv` in place of `& $identityUv`. Uvicorn's CLI flag controls framework/server logging, while `IDENTITY_LOG_LEVEL` controls the application's credential-safe JSON events. Keep Uvicorn's raw access logs disabled; the application supplies safe structured request logs. Stop the foreground process with Ctrl+C.

The private `/health/live`, `/health/ready`, and `/metrics` endpoints are registered; see [health probes](health-probes.md) and [observability](observability.md) for responses/manual checks. Other routes return the shared 404 error envelope, and generated Swagger/ReDoc/OpenAPI endpoints are disabled. [HTTP handling](http-contract.md) supplies global response headers, validation guards, safe errors, and CORS.

## Lifecycle and transactions

`create_app()` validates settings and returns an independent app. Its lifespan creates database resources on startup and disposes of the connection pool on shutdown. Engine construction is lazy: a PostgreSQL outage does not prevent application startup. Connection attempts happen only when database work is requested, including readiness checks; startup performs neither migrations nor a Google login.

`Database.transaction()` supplies a SQLAlchemy session, commits on normal completion, rolls back on an exception, and closes the session. Future business changes and their outbox writes must share this transaction. SQL parameters are hidden in SQLAlchemy errors and SQL echo is disabled. Real database transaction behavior will be verified by PostgreSQL integration tests in the persistence step.

Tests can supply a `Settings` instance and a `database_factory` callable to `create_app()`. The factory returns an object with `close()` and `check_readiness()`; this keeps lifecycle and request tests independent of live infrastructure. An optional `log_stream` supports isolated structured-log checks. Resource creation/disposal and readiness database work run in worker threads. Resource references are cleared on shutdown even if cleanup fails; propagated lifecycle errors use constant messages to keep server-managed tracebacks credential-safe.

## Verification commands

The [test guide](testing.md) covers all three suites and their prerequisites, isolation, and failure behavior.

```powershell
& $identityUv run --locked pytest tests/unit -q --junitxml=test-results/unit.xml
if ($LASTEXITCODE -ne 0) { throw "Unit tests failed." }
& $identityUv run --locked pytest tests/request -q --junitxml=test-results/request.xml
if ($LASTEXITCODE -ne 0) { throw "Request tests failed." }
```

Tests isolate local environment variables and working directories, use synthetic credentials, and require no database, broker, or Google account. Warnings are treated as errors, and pytest's default nonzero exit for empty suites is preserved. Request tests use FastAPI/Starlette TestClient with HTTPX2, the client required by the selected Starlette version; see [Starlette's test-client documentation](https://starlette.dev/testclient/).

The current suites verify safe configuration errors, environment-file precedence, timeout bounds, URL restrictions, lazy database creation, session commit/rollback management, application isolation, startup/shutdown cleanup, and [health behavior](health-probes.md#verification). Business endpoint coverage will accompany its implementation.

## Verification recorded for Step 5

- Unit suite: 42 tests passed.
- Request suite: 6 tests passed.
- A real Uvicorn factory startup served a local HTTP request (the expected 404 before endpoint implementation) and shut down cleanly while database connections were forbidden by a controlled test double.
- Credential-safe configuration errors and lifecycle cleanup were verified without live PostgreSQL or Google/broker dependencies.

This records Step 5's application-foundation verification; [database setup](database-setup.md#verification-recorded-for-step-6), [health probes](health-probes.md#verification), [HTTP handling](http-contract.md), [observability](observability.md), the [container guide](container.md), and the [CI guide](ci.md) record subsequent work. Hosted CI verification remains pending.

Related documents: [environment setup](environment-setup.md), [stack decisions](stack-decisions.md), and [contract requirements](contract-requirements.md).
