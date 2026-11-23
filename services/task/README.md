# Task Service

Java/Spring Boot Task service. Board and task CRUD, task assignment and moves, and immutable comments with explicit mentions are implemented with workspace authorization and service-owned PostgreSQL persistence. The Task outbox publisher and workspace lifecycle cleanup consumer are implemented.

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

The script requires `.env`, stops and removes the stack containers and network, and preserves volumes. To intentionally reset the local database, run `docker compose --profile app down --volumes` directly; this deletes the database contents. The broker is optional and its availability does not participate in readiness checks.

## Configuration

| Variable | Default / requirement |
| --- | --- |
| `TASK_DB_URL` | `jdbc:postgresql://localhost:5433/task_db?currentSchema=task_service` |
| `TASK_DB_USERNAME` | `task_app` |
| `TASK_DB_PASSWORD` | Required; no application default |
| `IDENTITY_BASE_URL` | `http://localhost:8000`; HTTP(S) origin, no path, credentials, query, or fragment |
| `TASK_IDENTITY_SERVICE_KEY` | Task-specific Identity service credential; unset by default. Protected requests return 503 until configured |
| `TASK_CURSOR_SECRET` | Secret of at least 32 UTF-8 bytes, shared by all Task instances; task and comment lists return 503 until configured |
| `SERVER_PORT` | `8001` |
| `TASK_CONTAINER_IDENTITY_BASE_URL` | Compose app only: `http://host.docker.internal:8000` |

Local Compose provisions credentials from `.env`; changing a password in `.env` does not change credentials already persisted in the PostgreSQL volume. Use a proper database credential update or intentionally reset disposable local data.

Flyway creates the private `task_service` schema and records applied versions. `V1__initialize_task_schema.sql` establishes the namespace only; domain tables belong to their feature migrations. Add new versioned SQL files in `src/main/resources/db/migration/`; do not edit migrations already deployed. No tables in another service's database are accessed.

Logs use Spring's ECS JSON format with the application name; `request_id` is attached to the logging context during HTTP requests. Responses include `X-Request-ID` and `Cache-Control: no-store`. The private `/actuator/prometheus` endpoint exports JVM/application/HTTP metrics. Keep both health probes and metrics off the public gateway as required by the routing contract. Publisher metrics are described below.

## Workspace authorization

Future workspace-scoped handlers must declare `@RequireWorkspaceAccess` and include a `workspace_id` path variable; set `owner = true` for owner-only operations. Inject `WorkspaceAccess` into the handler to use the verified actor, workspace version, members and membership IDs. The annotation is opt-in, so each new protected route must include request-level tests proving the guard runs before mutation. Apply local resource nesting and tombstone checks as domain features are added.

Each request checks Identity using the original bearer and Task's configured service key, forwarding the request ID. No positive result is cached. Checks use a two-second timeout per attempt with at most one retry for transport failures or Identity 503. Invalid/revoked user sessions return 401, inaccessible workspaces 404, and owner denial 403. An absent or rejected service key, invalid snapshot, or unavailable Identity returns 503 with `Retry-After`; no mutation may proceed. Health endpoints remain independent of the service key.

When Identity implements the private access endpoint, configure `IDENTITY_BASE_URL` and `TASK_IDENTITY_SERVICE_KEY` with the real Task-specific secret on a private network (TLS when crossing hosts). With a valid user bearer and workspace UUID, invoke the board list route and verify membership succeeds, then verify board deletion denies a member and revoked sessions and removed/deleted workspaces fail. Stop Identity or use an invalid service key and verify 503 and no mutation. Never put credentials in URLs, logs, committed files, or test fixtures. The automated tests use controlled Identity responses and do not require a live Identity service.

## Boards

Current workspace members can list, create, retrieve and rename boards; only the owner can delete them. The five-active-board limit is serialized per workspace in PostgreSQL. Create requires a UUIDv4 `Idempotency-Key`: retries by the same actor and path replay the original response for at least 24 hours after authorization and visibility checks. Reusing a key with a different normalized name returns 409. PATCH and DELETE require a quoted version in `If-Match`; actual renames increment the version, while identical names preserve it. Deleted boards disappear and release their quota slot.

Board deletion is a soft delete. Task reads enforce active board visibility, and task mutations synchronize with board deletion. Comment reads and creation also enforce active board and task visibility under the workspace lock.

## Tasks

Members can list, create, retrieve, update, move and delete tasks in accessible workspaces. Tasks have workspace-unique, monotonically allocated numbers; moves preserve the ID and number. Lists use descending creation time and ID, with signed cursors bound to actor, workspace and board filter. Set `TASK_CURSOR_SECRET` to a securely generated secret of at least 32 UTF-8 bytes, identical across replicas; rotating it invalidates existing cursors. Do not commit the secret.

Create requires an idempotency key; retries return the original response after checking current access and active parents. Updates and deletes require `If-Match`. Task reads reconcile assignment membership generations with a fresh Identity snapshot, updating the task version when clearing a removed membership. Assignment to another member writes a pending `task.assigned.v1` outbox record in the same transaction. The outbox publisher sends confirmed events to RabbitMQ. The cleanup consumer uses the same workspace lock as protected operations and records removal/deletion tombstones.

Task and board deletion hide tasks and comments through parent visibility checks.

## Comments and mentions

Members can list and create immutable plain-text comments on visible tasks. There are no edit or delete comment routes. Comment lists use ascending creation time and ID, with signed cursors bound to the actor, workspace and task; they use the same `TASK_CURSOR_SECRET` as task lists. Comment creation requires an `Idempotency-Key`. Author and mentioned-user names are snapshots from creation, and comment creation does not change the task version.

The frontend sends explicit mention ranges in Unicode code points, measured in the exact untrimmed body. For example, the emoji below is one code point and the space is one, so `@Sam` begins at offset 2 and has length 4:

```json
{"body":"😀 @Sam", "mentions":[{"user_id":"00000000-0000-4000-8000-000000000002", "start":2, "length":4}]}
```

Selected mentions must identify current members, fit the body and not overlap. Literal `@` text without a selected range does not notify. Repeated ranges for one member produce one pending mention event per comment; self-mentions produce none. Comment and pending event records commit together. Historical comment snapshots remain unchanged by membership cleanup.

## Outbox publication

Publishing is opt-in and does not affect readiness or request success. Start and provision the existing [broker](../../infra/broker/README.md) first. Set `TASK_OUTBOX_ENABLED=true` and the Task account's `TASK_BROKER_PASSWORD` in the local `.env`; host, port, virtual host and username default to `localhost`, `5672`, `taskmgn` and `task`. Set `TASK_BROKER_TLS=true` across networks: Java's configured default trust store and hostname verification are used; install the broker CA in the runtime trust store. Credentials belong in a secret store, never source control.

For a host-run service, `./scripts/dev.sh` connects to the broker's loopback port. For a container on Linux or Docker Desktop, attach it to the broker's existing private Compose network after provisioning:

```sh
docker compose -f compose.yaml -f compose.broker.yaml --profile app up -d --build
```

This override uses `broker:5672` on the existing `taskmgn-broker_default` network; it does not declare broker topology. The regular `all-up.sh` does not attach that network. `host.docker.internal` may work with Docker Desktop, but Linux's host gateway cannot reach a listener bound only to host loopback. Keep the broker port private; do not expose it publicly to work around this.

The publisher takes one pending row lock per bounded attempt, sends persistent mandatory messages, and marks the row published only after a positive confirm and no return. Failures remain pending with exponential backoff (1–60 seconds plus bounded jitter); crash after confirmation can cause a duplicate with the same event ID and payload. Consumers must deduplicate. Pending and confirmed records are retained indefinitely; no purge is implemented. The `task.outbox.pending`, `task.outbox.oldest.seconds`, `task.outbox.publish.failures`, and `task.outbox.published` metrics and correlated event/request IDs in logs support operations. A pending age over five minutes is logged. Investigate broker credentials, routes and connectivity, then restore service; retry is automatic. Notification is not implemented yet, so production notification delivery and deduplication cannot yet be verified end to end.

## Lifecycle cleanup

Set `TASK_CLEANUP_ENABLED=true` along with the broker settings above to consume `task-cleanup.v1`; both publisher and consumer are independently opt-in. Provision the existing broker before enabling the consumer. It uses the Task account, a prefetch of one and manual acknowledgements; it never declares broker topology. Broker availability does not change readiness or the result of an already committed API operation.

Identity remains authoritative for access: Task checks it on each protected request even before a cleanup event arrives. Once a member-removal event is processed, Task records the exact membership-generation tombstone and clears only matching task assignments, incrementing affected versions; historical comment names and mention snapshots remain unchanged. Workspace deletion records a terminal tombstone, hiding its boards, tasks and comments. Protected operations and cleanup synchronize on the same workspace lock, so a stale checked request cannot recreate deleted state after cleanup. Markers and tombstones are retained indefinitely.

Invalid events are forwarded to `task-cleanup.v1.dlq`. Transient failures use the provisioned 5s, 30s, 2m, 10m and 30m queues, then the sixth failed attempt goes to the dead-letter queue. Forwarding is mandatory and confirmed before acknowledging the original; an unconfirmed transfer leaves it available for redelivery. Inspect `task.cleanup.consumed`, `task.cleanup.duplicate`, `task.cleanup.failures`, `task.cleanup.retries`, `task.cleanup.deadletter`, `task.cleanup.malformed` and `task.cleanup.transport.failures` metrics and sanitized event/request IDs in logs. Repair the cause before replaying through the [broker operator tool](../../infra/broker/README.md); replay preserves event identity and deduplication prevents repeated effects.

To verify the permission boundary with the real Identity service in an isolated environment, use separate disposable Identity and Task databases and the provisioned shared broker. Use only synthetic users and sessions, such as the `registered_actor` test fixture in `services/identity-workspace/tests/integration/conftest.py`; do not use production accounts or credentials.

1. Start Identity and its outbox worker, and Task with `TASK_CLEANUP_ENABLED=false`. Configure Task's private Identity service key and broker credentials. Create a workspace, invite and accept a member, then create a board and a task assigned to that member.
2. Remove the member through Identity. Before enabling cleanup, request the task using the removed member's session: the fresh Identity access check must return 404. The queued removal event has not yet caused Task's cleanup effect.
3. Enable the Task consumer and verify the task's assignment becomes null and its version increases; redeliver the same event and verify the version does not change again. For the workspace case, pause cleanup, delete the workspace in Identity and verify Task denies access before resuming cleanup; after delivery verify the workspace tombstone exists in Task's own database. Check historical comments remain unchanged. The automated integration tests exercise duplicate, rejoin, rollback and in-flight race cases with disposable services.

## Tests and CI

```sh
# Isolated unit tests, then dedicated MockMvc endpoint tests: no Docker or live services.
./mvnw --batch-mode --no-transfer-progress test

# Also run real PostgreSQL and RabbitMQ migration, board/task/comment concurrency and visibility, publisher and cleanup delivery/retry, cursor, reconciliation, and startup/readiness tests; Docker is required.
./mvnw --batch-mode --no-transfer-progress verify -Pintegration
```

Unit and endpoint suites are separate Surefire executions, each configured to fail when empty. The integration profile uses Failsafe with the same empty-suite protection. It creates an ephemeral PostgreSQL container and an in-process Identity HTTP test double; it does not use a local development database or live Identity/Google credentials. Docker unavailability fails integration tests rather than silently skipping them.

Coverage includes credential forwarding, validated access snapshots, no caching, authorization and dependency failures that prevent mutations, liveness independence, ready/unavailable responses, database and Identity failures, correlation headers, bounded health probes, unsupported health methods, real Flyway migration/restart-safe execution, board request validation and authorization, quota and task-number races, idempotency replay, version conflicts, signed cursor scope, assignment reconciliation, mention ranges and snapshots, atomic pending outbox writes, mandatory returns, broker restart, retries, cleanup deduplication, tombstones and confirmed delivery. Reports are under `target/surefire-reports/`, `target/endpoint-test-reports/`, and `target/failsafe-reports/`.

[Task CI](../../.github/workflows/task-service.yml) runs all three suites on pull requests, pushes to `main`, and manual dispatch; it currently runs on every change, including contracts, to avoid skipped required checks. The container job requires successful tests. CI uploads reports and builds the image; it does not yet publish or deploy. Registry publication follows registry/deployment configuration and must remain gated by tests. Configure required GitHub checks separately; workflow files do not enforce branch protection themselves.

Every subsequent domain endpoint needs its own unit and request-level tests in its implementation PR. Follow the [shared testing policy](../../CONTRIBUTING.md#endpoint-tests-and-ci-requirements).

## Build an image

```sh
./scripts/build.sh
```

This builds only the `task-service:local` image and does not require `.env` or start containers. The multistage image uses Java 25 and a non-root runtime user. Its build context is this service directory. The Docker packaging step skips tests because CI runs the complete suites before this job; manually run the verification command above before publishing. Base image tags support architecture-specific builds; verify/build an ARM64 image for Oracle ARM instances. No registry credentials, local `.env`, or generated `target/` content enter the build context.
