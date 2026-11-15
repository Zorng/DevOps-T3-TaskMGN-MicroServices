# Task Service

Java/Spring Boot scaffold for the Task work area. Implemented: application configuration, service-owned migration namespace, health probes, request correlation, JSON logs, metrics endpoint, local PostgreSQL setup, tests, and CI. Boards, tasks, authorization, comments, outbox publication, and cleanup remain subsequent features; no domain API is implemented yet.

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

No contracts were changed by the scaffold. Readiness calls Identity's private operational `/health/ready`; it is not the per-request authorization check. The authorization client and business endpoints are future work. The readiness probe carries no user/session credentials and never performs a real login.

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
| `SERVER_PORT` | `8001` |
| `TASK_CONTAINER_IDENTITY_BASE_URL` | Compose app only: `http://host.docker.internal:8000` |

Local Compose provisions credentials from `.env`; changing a password in `.env` does not change credentials already persisted in the PostgreSQL volume. Use a proper database credential update or intentionally reset disposable local data.

Flyway creates the private `task_service` schema and records applied versions. `V1__initialize_task_schema.sql` establishes the namespace only; domain tables belong to their feature migrations. Add new versioned SQL files in `src/main/resources/db/migration/`; do not edit migrations already deployed. No tables in another service's database are accessed.

Logs use Spring's ECS JSON format with the application name; `request_id` is attached to the logging context during HTTP requests. Responses include `X-Request-ID` and `Cache-Control: no-store`. The private `/actuator/prometheus` endpoint exports JVM/application/HTTP metrics. Keep both health probes and metrics off the public gateway as required by the routing contract. Domain-specific outbox metrics will follow that implementation.

## Tests and CI

```sh
# Isolated unit tests, then dedicated MockMvc endpoint tests: no Docker or live services.
./mvnw --batch-mode --no-transfer-progress test

# Also run real PostgreSQL migration/startup/readiness tests; Docker is required.
./mvnw --batch-mode --no-transfer-progress verify -Pintegration
```

Unit and endpoint suites are separate Surefire executions, each configured to fail when empty. The integration profile uses Failsafe with the same empty-suite protection. It creates an ephemeral PostgreSQL container and an in-process Identity HTTP test double; it does not use a local development database or live Identity/Google credentials. Docker unavailability fails integration tests rather than silently skipping them.

Coverage includes liveness independence, ready/unavailable responses, database and Identity failures, correlation headers, bounded health probes, unsupported health methods, and real Flyway migration/restart-safe execution. Reports are under `target/surefire-reports/`, `target/endpoint-test-reports/`, and `target/failsafe-reports/`.

[Task CI](../../.github/workflows/task-service.yml) runs all three suites on pull requests, pushes to `main`, and manual dispatch; it currently runs on every change, including contracts, to avoid skipped required checks. The container job requires successful tests. CI uploads reports and builds the image; it does not yet publish or deploy. Registry publication follows registry/deployment configuration and must remain gated by tests. Configure required GitHub checks separately; workflow files do not enforce branch protection themselves.

Every subsequent domain endpoint needs its own unit and request-level tests in its implementation PR. Follow the [shared testing policy](../../CONTRIBUTING.md#endpoint-tests-and-ci-requirements).

## Build an image

```sh
./scripts/build.sh
```

This builds only the `task-service:local` image and does not require `.env` or start containers. The multistage image uses Java 25 and a non-root runtime user. Its build context is this service directory. The Docker packaging step skips tests because CI runs the complete suites before this job; manually run the verification command above before publishing. Base image tags support architecture-specific builds; verify/build an ARM64 image for Oracle ARM instances. No registry credentials, local `.env`, or generated `target/` content enter the build context.
