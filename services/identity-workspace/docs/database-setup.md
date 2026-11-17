# Identity & Workspace Database and Migrations

The default development setup uses a dedicated PostgreSQL 18.7 Compose container on `127.0.0.1:5434`. A Windows PostgreSQL installation on port 5432 can stay running independently. No administrator password from that installation is needed by this setup.

## Ownership and storage

- Database: `identity_db`.
- Application/migration role: `identity_app`, with login but no superuser, create-database, create-role, or replication privilege. It owns this service's database so it can apply migrations. A separate runtime/migration role split can be added for deployment.
- Bootstrap administrator: `identity_owner`, used only for initializing this isolated cluster.
- Service tables: schema `identity_workspace`. Alembic tracks the migration revision in this database's `public.alembic_version` table.
- Persistent Compose volume: `identity-workspace-local_identity-db`, mounted at `/var/lib/postgresql`, as required by the [PostgreSQL 18 official image](https://hub.docker.com/_/postgres).
- Image: `postgres:18.7@sha256:2ea8630df176ad090ec92136dee850397c5af8781e4bcf2e4162593691c61bbc`. The digest pins the image content; application ARM64 execution remains future verification.

The database is exclusively owned by Identity. Other services integrate through REST/events and never read its tables.

## Start the local database

Run from `services/identity-workspace/` after [environment setup](environment-setup.md), with Docker Desktop running:

```powershell
$identityUv = Join-Path $PWD '.tools/uv/bin/uv.exe'
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
& $identityUv run --locked python scripts/setup_local_db.py
if ($LASTEXITCODE -ne 0) { throw "Local database setup failed." }
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Invalid Compose configuration." }
docker compose up --detach --wait --wait-timeout 60 db
if ($LASTEXITCODE -ne 0) { throw "Database startup failed." }
```

Use `uv` instead of `& $identityUv` if the pinned version is on PATH. The setup script generates distinct random passwords into ignored `.secrets/database-owner-password` and `.secrets/database-app-password` files. It creates an ignored `.env` with the application connection URL when the file does not already exist. It never prints passwords or replaces existing configuration/credentials.

Compose mounts the password files as secrets; committed Compose configuration and command arguments contain secret-file paths, not password values. A small entrypoint wrapper reads the app secret before the official image changes users, allowing private host-file permissions on Linux. Initialization uses psql's quoted variable substitution to create the app role safely.

Step 11 also generates an ignored `.env.container` using the same application password and `db:5432` for Docker networking, preserving any existing file. The [container guide](container.md) documents the app profile, runtime environment, and one-off migration container. Database-only commands below remain available without starting the application.

The PostgreSQL healthcheck confirms the server accepts connections. It does not replace the service [readiness probe](health-probes.md), which also checks the migration revision, writability, and outbox permissions. If `.env` already existed, verify its database URL matches the database you intend to use before applying migrations.

## Apply and inspect migrations

```powershell
& $identityUv run --locked alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }
& $identityUv run --locked alembic current
& $identityUv run --locked alembic check
```

Expected revision: `0005_registered_invitations (head)`. `alembic check` should report no new upgrade operations. Repeating `upgrade head` is safe. Migrations run explicitly, once before application startup; application workers do not create or alter tables automatically. See [Alembic's migration tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html).

`alembic.ini` contains no URL or credentials. Online migrations use the same validated settings and bounded database connection/query timeouts as the application. SQL parameters are hidden, and URL passwords are not interpolated through INI configuration. Offline SQL generation needs no database credentials:

```powershell
& $identityUv run --locked alembic upgrade head --sql
```

Future migrations belong in `migrations/versions/`; update the schema metadata in `src/identity_workspace/persistence.py` with the corresponding change. Review generated migrations before running them. Downgrade is implemented and tested only against disposable test databases; downgrading this initial migration removes its schema and pending records.

## Initial outbox foundation

The first migration creates the service schema and `identity_workspace.outbox`. It stores:

- Stable UUID event ID and event type.
- The JSON event envelope/payload.
- Creation and next-attempt timestamps, attempt count, and optional publication timestamp.
- A partial index for pending events.

Constraints reject duplicate event IDs, negative attempt counts, non-Identity event types, inconsistent/null envelope identity fields, and payloads whose PostgreSQL JSON text representation exceeds 65,536 bytes. The JSONB storage bound is conservative; future publishers must also enforce the contract's encoded-message size limit.

Use `Database.transaction()` for both a future business change and its outbox insert. A local commit retains the pending event without requiring a broker. A rollback removes both effects. Full event-schema validation, immutable publication payload handling, publisher confirmations, retries, retention, and broker delivery will accompany the producer/publisher tasks; this migration is storage groundwork, not an implemented event producer.

No user, session, workspace, membership, or invitation business tables are introduced by this foundation. Test-only transaction probe tables exist solely inside disposable integration databases.

The follow-up [authentication migration](google-signin.md#flow-and-persistence), `0002_google_sessions`, adds users, challenge hashes, session hashes, and shared rate-limit buckets while preserving outbox storage. It introduces no workspace/membership/invitation tables. Session/challenge lifetimes and Google-subject identity constraints are enforced in PostgreSQL; the authentication guide documents bounded cleanup.

The current [workspace migration](workspace-management.md#persistence-and-concurrency), `0003_workspace_management`, adds workspaces, membership generations, shared invitation lifecycle storage, successful-create records, and authenticated opaque cursors. Workspace deletion now produces a real outbox event in the same transaction; a trigger makes its identity/payload immutable while allowing delivery bookkeeping updates. Invitation endpoints and broker delivery remain separate integration tasks. The earlier foundation description above records what migration `0001` introduced.

The [membership follow-up](membership-permissions.md), `0004_membership_generations`, preserves these rows and adds a trigger protecting immutable generation fields and terminal revocation. Owner removal and non-owner leave now produce generation-specific removal events in the existing outbox. The membership guide documents shared join/access primitives and the remaining delivery/cleanup integration.

The [registered-user invitation migration](registered-user-invitations.md), `0005_registered_invitations`, adds invitation page cursors/list indexes, protects invitation identity/terminal outcomes, and allows null ETags for successful invitation creates (which have no ETag). Existing invitation, membership, session, retry and outbox data is preserved. Invitation APIs now use this shared storage; final broker/consumer delivery remains separate.

## Persistence and troubleshooting

```powershell
docker compose stop db
docker compose up --detach --wait --wait-timeout 60 db
```

The named volume retains the database, migration revision, and pending outbox records across restart. `docker compose down` removes this project's containers/network and preserves the named volume. The database is left running after the initial verification so it is ready for local development.

Initialization scripts run only on an empty database volume. Regenerating password files does not rotate existing database-role passwords; preserve the matching ignored files with the development database. Repeating the setup script preserves those files and any existing `.env`.

The default host port is 5434. If it is occupied, set `$env:IDENTITY_DB_PORT` to an available port before starting Compose and update the ignored application URL to match. `IDENTITY_DB_PORT` and `IDENTITY_DB_SECRETS_DIR` are Compose-only overrides supplied through the shell; do not add them to the application's strict `.env` file.

## Use your installed PostgreSQL instead

This is optional; the verified Compose setup already provides the development database. To use the installed server on port 5432, create the dedicated role/database through an administrator connection in SQL Shell or pgAdmin:

```sql
CREATE ROLE identity_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
```

In psql, set its password interactively using `\password identity_app`, then run:

```sql
CREATE DATABASE identity_db OWNER identity_app;
REVOKE ALL ON DATABASE identity_db FROM PUBLIC;
GRANT CONNECT ON DATABASE identity_db TO identity_app;
```

Set your ignored `.env` URL to the native server, chosen app-role password, and port 5432, then run the same Alembic commands. These commands are for a new role/database; inspect any existing resources first. Integration tests always create their own disposable container and never use this connection URL.

## Integration tests

```powershell
& $identityUv run --locked pytest tests/integration -q --junitxml=test-results/integration.xml
if ($LASTEXITCODE -ne 0) { throw "Database integration tests failed." }
```

Docker must be available; missing infrastructure fails the suite rather than silently skipping it. The fixture creates a uniquely named Compose project with random credentials and an automatically assigned localhost port, uses the real bootstrap/migrations, and removes only its own test containers/network/volume afterward. It supplies an empty Compose environment file so it cannot target the developer database. After container restart it rediscovers the assigned test port.

The tests cover PostgreSQL version/role privileges, migration upgrade/repeat/downgrade, schema drift, durable pending events, atomic business/outbox commit and rollback, duplicate-event rejection, storage constraints, statement cancellation/recovery, and persistence through database restart. Unit tests additionally verify setup does not overwrite configuration or credentials and does not print their values. Test reports are ignored, and default short tracebacks avoid dumping driver call arguments.

## Verification recorded for Step 6

- Local Compose database started successfully on localhost:5434.
- Migration applied and repeated successfully; revision is `0001_identity_foundation`.
- `alembic check` reported no schema drift.
- Unit suite: 45 passed; request suite: 6 passed; PostgreSQL integration suite: 16 passed, with zero skips.
- The disposable integration database was removed afterward; the separate local development database remains available.

The original README is retained. Subsequent results cover [health/readiness endpoints](health-probes.md), [shared HTTP handling](http-contract.md), [observability](observability.md), the [service container](container.md), and the [CI workflow](ci.md). Hosted CI verification remains pending.

Related documents: [application startup](application-startup.md), [environment setup](environment-setup.md), [stack decisions](stack-decisions.md), and [contract requirements](contract-requirements.md).
