# Task Service

Java/Spring Boot Task service. Board listing, creation, retrieval, renaming, and deletion are implemented with workspace authorization and service-owned PostgreSQL persistence. Tasks, comments, outbox publication, and workspace cleanup remain subsequent features.

## Stack

| Component | Selection | Purpose |
| --- | --- | --- |
| Java | 25 | Shared local/CI/container language baseline |
| Spring Boot | 4.1.2 | MVC HTTP service, dependency management, configuration |
| Maven | 3.9.11 via wrapper | Repeatable commands without a global Maven install |
| PostgreSQL | 17; local/test image `postgres:17.6` | Service-owned relational database |
| Flyway | Managed by Spring Boot BOM | Versioned SQL migrations |
| Spring JDBC / HikariCP | Managed by Spring Boot BOM | Database connections and explicit SQL foundation |
| JUnit / Mockito / MockMvc | Managed by Spring Boot BOM | Isolated unit and HTTP request tests |
| Testcontainers | Managed by Spring Boot BOM | Disposable real PostgreSQL integration tests |

The Maven parent pins the dependency/plugin BOM, and wrapper properties pin Maven. No snapshot or dynamic third-party dependency versions are selected. The service version itself is a development SNAPSHOT. Local Java 25, Docker, and Docker Compose are prerequisites; Maven's first execution requires network access to download dependencies.

The selected framework supports this Java version; see [Spring Boot system requirements](https://docs.spring.io/spring-boot/system-requirements.html). Migration and testing follow the [Spring Boot migration guidance](https://docs.spring.io/spring-boot/how-to/data-initialization.html) and [Testcontainers PostgreSQL module](https://java.testcontainers.org/modules/databases/postgres/).

## Scope and contracts

The service will own boards, tasks, assignments, comments, mentions, board limits, task numbering, and task cleanup. Workspace identity and membership stay in Identity & Workspace. Service databases are not shared.

- [Task REST contract](../../api_contracts/openapi/task.openapi.json)
- [Private Identity contract](../../api_contracts/openapi/identity-internal.openapi.json)
- [Authentication](../../api_contracts/authentication.md)
- [Workflows](../../api_contracts/workflows.md)
- [Event contracts](../../api_contracts/events/README.md)
- [Acceptance scenarios](../../api_contracts/acceptance-scenarios.md)

No contracts were changed by the scaffold. Readiness calls Identity's private operational `/health/ready`; it is not the per-request authorization check. Board routes use the per-request workspace access client and reusable guard. The readiness probe carries no user/session credentials and never performs a real login.

## Run locally

All commands below run from `services/task/`. On macOS with several JDKs installed, select Java 25 first:

```sh
export JAVA_HOME="$(/usr/libexec/java_home -v 25)"
./mvnw --version
cp .env.example .env
```

Replace the placeholder password in `.env`. Do not commit it. Start just the isolated local database:

```sh
docker compose up -d --wait db
```

Launch Spring Boot with the helper, which exports the trusted local `.env` values and checks the required database settings:

```sh
./scripts/dev.sh
```

Alternatively, export the trusted `.env` values with `set -a; . ./.env; set +a` and run `./mvnw spring-boot:run` directly.

Task listens on port 8001; PostgreSQL is bound to localhost port 5433. Flyway applies migrations at startup. Database unavailability at startup prevents migration/startup; after startup, health endpoints report dependency outages as described below.

To build and run both database and app as containers instead:

```sh
./scripts/all-up.sh
```

This requires `.env`, builds the app image, and starts the database and app in the background. Compose waits for the database health check; the app has no Compose health check, so this does not verify app readiness.

The container's JDBC URL uses the Compose `db` hostname. Identity defaults to the host's port 8000 through `host.docker.internal`; override `TASK_CONTAINER_IDENTITY_BASE_URL` for another deployment. Do not set it to the Task service itself. This is a Task-only development stack, not the full application Compose environment.

```sh
curl -i http://localhost:8001/health/live
curl -i http://localhost:8001/health/ready
```

Liveness returns 200 with `{"status":"ok"}` without checking dependencies. Readiness returns 200 only when PostgreSQL validates and Identity answers its health probe with 200; otherwise it returns 503 with `{"status":"unavailable"}`. **503 is expected while the real Identity service is not running.** A bounded credential-free probe checks it; no dummy healthy Identity service is included in production/local configuration.

Stop the local stack while preserving database data:

```sh
./scripts/all-down.sh
```

The script requires `.env`, stops and removes the stack containers and network, and preserves volumes. To intentionally reset the local database, run `docker compose --profile app down --volumes` directly; this deletes the database contents. No broker is configured yet, and broker availability does not participate in this scaffold's readiness checks.

## Configuration

| Variable | Default / requirement |
| --- | --- |
| `TASK_DB_URL` | `jdbc:postgresql://localhost:5433/task_db?currentSchema=task_service` |
| `TASK_DB_USERNAME` | `task_app` |
| `TASK_DB_PASSWORD` | Required; no application default |
| `IDENTITY_BASE_URL` | `http://localhost:8000`; HTTP(S) origin, no path, credentials, query, or fragment |
| `TASK_IDENTITY_SERVICE_KEY` | Task-specific Identity service credential; unset by default. Protected requests return 503 until configured |
| `SERVER_PORT` | `8001` |
| `TASK_CONTAINER_IDENTITY_BASE_URL` | Compose app only: `http://host.docker.internal:8000` |

Local Compose provisions credentials from `.env`; changing a password in `.env` does not change credentials already persisted in the PostgreSQL volume. Use a proper database credential update or intentionally reset disposable local data.

Flyway creates the private `task_service` schema and records applied versions. `V1__initialize_task_schema.sql` establishes the namespace only; domain tables belong to their feature migrations. Add new versioned SQL files in `src/main/resources/db/migration/`; do not edit migrations already deployed. No tables in another service's database are accessed.

Logs use Spring's ECS JSON format with the application name; `request_id` is attached to the logging context during HTTP requests. Responses include `X-Request-ID` and `Cache-Control: no-store`. The private `/actuator/prometheus` endpoint exports JVM/application/HTTP metrics. Keep both health probes and metrics off the public gateway as required by the routing contract. Domain-specific outbox metrics will follow that implementation.

## Workspace authorization

Future workspace-scoped handlers must declare `@RequireWorkspaceAccess` and include a `workspace_id` path variable; set `owner = true` for owner-only operations. Inject `WorkspaceAccess` into the handler to use the verified actor, workspace version, members and membership IDs. The annotation is opt-in, so each new protected route must include request-level tests proving the guard runs before mutation. Apply local resource nesting and tombstone checks as domain features are added.

Each request checks Identity using the original bearer and Task's configured service key, forwarding the request ID. No positive result is cached. Checks use a two-second timeout per attempt with at most one retry for transport failures or Identity 503. Invalid/revoked user sessions return 401, inaccessible workspaces 404, and owner denial 403. An absent or rejected service key, invalid snapshot, or unavailable Identity returns 503 with `Retry-After`; no mutation may proceed. Health endpoints remain independent of the service key.

When Identity implements the private access endpoint, configure `IDENTITY_BASE_URL` and `TASK_IDENTITY_SERVICE_KEY` with the real Task-specific secret on a private network (TLS when crossing hosts). With a valid user bearer and workspace UUID, invoke the board list route and verify membership succeeds, then verify board deletion denies a member and revoked sessions and removed/deleted workspaces fail. Stop Identity or use an invalid service key and verify 503 and no mutation. Never put credentials in URLs, logs, committed files, or test fixtures. The automated tests use controlled Identity responses and do not require a live Identity service.

## Boards

Current workspace members can list, create, retrieve and rename boards; only the owner can delete them. The five-active-board limit is serialized per workspace in PostgreSQL. Create requires a UUIDv4 `Idempotency-Key`: retries by the same actor and path replay the original response for at least 24 hours after authorization and visibility checks. Reusing a key with a different normalized name returns 409. PATCH and DELETE require a quoted version in `If-Match`; actual renames increment the version, while identical names preserve it. Deleted boards disappear and release their quota slot.

Board deletion is a soft delete. Future task/comment reads must check active parent visibility, and descendant mutations must call the locking parent lookup within the same transaction so deletion cannot race their commit. Those features must add their own integration tests for descendant hiding; no task or comment schema exists yet.

## Tests and CI

```sh
# Isolated unit tests, then dedicated MockMvc endpoint tests: no Docker or live services.
./mvnw --batch-mode --no-transfer-progress test

# Also run real PostgreSQL migration, board concurrency/idempotency/version, and startup/readiness tests; Docker is required.
./mvnw --batch-mode --no-transfer-progress verify -Pintegration
```

Unit and endpoint suites are separate Surefire executions, each configured to fail when empty. The integration profile uses Failsafe with the same empty-suite protection. It creates an ephemeral PostgreSQL container and an in-process Identity HTTP test double; it does not use a local development database or live Identity/Google credentials. Docker unavailability fails integration tests rather than silently skipping them.

Coverage includes credential forwarding, validated access snapshots, no caching, authorization and dependency failures that prevent mutations, liveness independence, ready/unavailable responses, database and Identity failures, correlation headers, bounded health probes, unsupported health methods, real Flyway migration/restart-safe execution, board request validation and authorization, quota races, idempotency replay and version conflicts. Reports are under `target/surefire-reports/`, `target/endpoint-test-reports/`, and `target/failsafe-reports/`.

[Task CI](../../.github/workflows/task-service.yml) runs all three suites on pull requests, pushes to `main`, and manual dispatch; it currently runs on every change, including contracts, to avoid skipped required checks. The container job requires successful tests. CI uploads reports and builds the image; it does not yet publish or deploy. Registry publication follows registry/deployment configuration and must remain gated by tests. Configure required GitHub checks separately; workflow files do not enforce branch protection themselves.

Every subsequent domain endpoint needs its own unit and request-level tests in its implementation PR. Follow the [shared testing policy](../../CONTRIBUTING.md#endpoint-tests-and-ci-requirements).

## Build an image

```sh
./scripts/build.sh
```

This builds only the `task-service:local` image and does not require `.env` or start containers. The multistage image uses Java 25 and a non-root runtime user. Its build context is this service directory. The Docker packaging step skips tests because CI runs the complete suites before this job; manually run the verification command above before publishing. Base image tags support architecture-specific builds; verify/build an ARM64 image for Oracle ARM instances. No registry credentials, local `.env`, or generated `target/` content enter the build context.
