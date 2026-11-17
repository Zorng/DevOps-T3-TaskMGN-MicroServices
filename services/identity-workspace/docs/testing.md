# Identity & Workspace Test Guide

The scaffold has separate unit, HTTP request, and real PostgreSQL integration suites. Run all three before submitting changes. These commands test the implemented foundation; business API and event delivery tests accompany their later implementation tasks.

[Google sign-in/session tests](google-signin.md#automated-verification) now cover all four authentication endpoints, offline signature/claim checks, and real database atomicity/concurrency. Pytest blocks the requests HTTP transport by default; tests inject a Google verifier or key-transport double and need no real provider credentials.

[Workspace tests](workspace-management.md#automated-verification) cover all five workspace endpoints with unit/request suites and real PostgreSQL quota/retry/stale-write races, rollback, pagination, authorization and atomic deletion. Persisted invitation fixtures exercise cancellation until invitation endpoints are implemented. Responses and deletion events are validated against shared JSON Schemas. Container smoke now exercises the authenticated workspace lifecycle using a synthetic session in its disposable database, including the pending deletion outbox record.

## Prerequisites

Complete [environment setup](environment-setup.md) to install Python 3.13.16, uv 0.13.8, and the locked development dependencies. Run commands from `services/identity-workspace/`.

Unit and request tests need no running database, Google account, broker, or other service. Integration tests require Docker Desktop with its Linux engine running. Docker must have access to the pinned PostgreSQL 18.7 image, either cached locally or downloadable on the first run.

No suite needs your native PostgreSQL password or the development `.env`. Tests clear `IDENTITY_` settings and use a temporary working directory to prevent accidental use of local application configuration.

## Run the suites in PowerShell

```powershell
$identityUv = Join-Path $PWD '.tools/uv/bin/uv.exe'
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'

& $identityUv run --locked pytest tests/unit -q --junitxml=test-results/unit.xml
if ($LASTEXITCODE -ne 0) { throw "Unit tests failed." }

& $identityUv run --locked pytest tests/request -q --junitxml=test-results/request.xml
if ($LASTEXITCODE -ne 0) { throw "Request tests failed." }

& $identityUv run --locked pytest tests/integration -q --junitxml=test-results/integration.xml
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL integration tests failed." }
```

If the pinned uv is on PATH, use `uv` instead of `& $identityUv`. Add `--offline` after `--locked` to prohibit dependency downloads once the locked environment/cache is available; this does not control Docker image pulls. Generated JUnit reports and caches are ignored by Git.

Pytest treats warnings as errors and checks configuration and marker names strictly. A test failure, collection error, or empty suite returns a nonzero exit code. Keep each suite invocation separate so an empty suite cannot be hidden by tests collected from another directory. The PowerShell checks above stop execution on any nonzero result.

## Coverage of the foundation

| Behavior | Unit tests | Request tests | PostgreSQL integration tests |
| --- | --- | --- | --- |
| `GET /health/live` | Dependency-free handler result | Exact body/status/headers; responds while readiness is blocked | Remains available during database failures and pool exhaustion |
| `GET /health/ready` | Explicit success, unavailable resources, sanitized probe exceptions | Database result mapped to 200/503; request IDs and safe failure body | Migrations, permissions, read-only storage, query/pool timeouts, outage and recovery |
| `GET /metrics` | Parsable response, isolated registry, bounded labels, counters and durations | Method/query guards, headers, correlation, last readiness result | Scraping succeeds while storage is unavailable and after recovery |
| HTTP contract and logging | Strict fields, body framing/limits, safe errors and JSON logs | UTF-8/JSON validation, 64 KiB byte boundary, CORS, routing, concurrent request IDs, credential exclusion | Storage errors reach the health response without driver details |
| Configuration and lifecycle | Safe settings errors, bounded driver options, lazy resources, cleanup | Starts without a database connection; safe startup/shutdown errors | Real application-role connections and recovery |
| Migrations and outbox storage | Transaction scope management and safe local setup | Not exposed as scaffold HTTP routes | Upgrade/repeat/downgrade, schema drift, constraints, atomic commit/rollback, duplicate IDs and restart durability |

The suites live in [unit](../tests/unit/), [request](../tests/request/), and [integration](../tests/integration/). Request-only fixture routes exercise reusable HTTP handling without introducing unfinished business endpoints into the application. No arbitrary coverage percentage is required by the repository; test observable behavior and applicable failure paths.

## Database isolation and cleanup

The [integration fixture](../tests/integration/conftest.py) creates a unique `identity-it-*` Compose project, a dedicated volume, generated temporary credentials, and a free localhost port. It applies real Alembic migrations as the non-superuser application role. The project has no broker, so successful readiness and durable pending outbox records are verified without broker availability.

Integration tests deliberately revoke and restore permissions, hold a database connection or schema lock, and stop/restart their own PostgreSQL container. Fixture cleanup removes only that uniquely named test project's containers, network, and volume. The separate development database on port 5434 and native Windows PostgreSQL installation on port 5432 are unaffected. Docker startup failure is a test failure rather than an automatic skip.

## Check empty-suite rejection

This optional check intentionally deselects every unit test and expects pytest exit code 5. It is a negative check, not a normal passing suite invocation, and writes no passing JUnit report:

```powershell
& $identityUv run --locked pytest tests/unit -q -k step10_intentional_empty_selection
if ($LASTEXITCODE -ne 5) { throw "Empty-suite rejection did not return the expected exit code 5." }
```

Do not add an empty-suite success flag or ignore test exit codes. The [container guide](container.md#automated-smoke-test) documents the separate real-image smoke command and its isolated database. The [CI guide](ci.md) documents the test matrix, JUnit guard, aggregate check, and image publishing gates. Local results alone do not establish a passing remote pipeline.

## Verification recorded for Step 10

Verified on 2026-11-15 with the locked environment:

- Unit suite: 139 passed.
- Request suite: 80 passed.
- PostgreSQL integration suite: 27 passed, including pool exhaustion/recovery and each required outbox permission.
- All 246 tests passed with zero failures, errors, or skips; each suite produced its own JUnit report.
- The intentional empty selection returned exit code 5.

Related documents: [health probes](health-probes.md), [HTTP handling](http-contract.md), [observability](observability.md), [database setup](database-setup.md), and [scaffold scope](scaffold-scope.md).
