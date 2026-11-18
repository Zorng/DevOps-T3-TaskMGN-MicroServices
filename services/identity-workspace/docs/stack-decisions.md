# Identity & Workspace Stack Decisions

Python + FastAPI is confirmed by the scaffold task; PostgreSQL 18.7 is the selected database. The supporting libraries and tools below are implementation choices, not requirements imposed by the API contracts.

| Component | Selection | Purpose |
| --- | --- | --- |
| Python | 3.13.16, 64-bit | Runtime baseline for development, CI, and containers |
| FastAPI | 0.163.0 | HTTP routing, dependency injection, and API application |
| Pydantic | 2.14.3 | Strict request/response models and validation |
| Pydantic Settings | 2.16.3 | Validated environment configuration for the next scaffold step |
| Uvicorn | 0.54.0 | ASGI server for FastAPI |
| PostgreSQL | 18.7; digest-pinned `postgres:18.7` in [Compose](../compose.yaml) | Service-owned relational database and disposable integration database |
| SQLAlchemy | 2.1.8 | Connection management, queries, and explicit transactions |
| Psycopg | 3.3.9, including matching binary distribution | PostgreSQL driver through SQLAlchemy's `postgresql+psycopg` dialect |
| Alembic | 1.20.1 | Versioned database migrations |
| uv / uv_build | 0.13.8 | Dependency resolution/locking and the pinned Python package build backend |
| pytest | 9.2.0 | Separate unit, request, and database integration suites |
| HTTPX2 | 2.16.0 | HTTP request tests through FastAPI/Starlette TestClient |
| Prometheus Python client | 0.26.0 | Private operational metrics |
| Python standard-library logging | Included with Python 3.13.16 | Structured JSON logs with explicit safe fields and credential redaction |
| Google Auth | 2.62.2, requests extra | Supported Google OAuth ID-token signature/issuer/audience/time verification |
| CacheControl | 0.14.4 | HTTP-header-aware public verification-key caching |
| email-validator | 2.4.0 | Provider email format validation without DNS/deliverability lookups |
| Pika | 1.4.4 | RabbitMQ reference adapter with mandatory routing and publisher confirms |
| jsonschema | 4.26.0, format extra | Runtime validation of lifecycle events against the bundled shared schema |
| RabbitMQ | 4.4.1 Alpine, digest pinned | Opt-in test/reference broker; shared deployment selection belongs to Infrastructure |

The [FastAPI release notes](https://fastapi.tiangolo.com/release-notes/#01630-2026-11-14) document the selected framework release. See [SQLAlchemy's Psycopg integration](https://docs.sqlalchemy.org/en/21/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg), [Alembic documentation](https://alembic.sqlalchemy.org/en/latest/), and [uv locking guidance](https://docs.astral.sh/uv/concepts/projects/sync/) for the supporting tooling. The versions above are resolved in [uv.lock](../uv.lock). Step 5 selects HTTPX2 because [Starlette's current test client](https://starlette.dev/testclient/) deprecates plain HTTPX. Locked installation, dependency consistency, imports, unit/request tests, real PostgreSQL 18.7 integration tests, and [health endpoints](health-probes.md) are verified with the Windows application environment. Step 11 verifies the [service image](container.md) on Linux/amd64; ARM64 application execution remains unverified.

## Dependency reproducibility

Keep the service's runtime and development dependencies in its own [pyproject.toml](../pyproject.toml), with exact resolved direct/transitive versions and distribution hashes committed in [uv.lock](../uv.lock). The manifest restricts dependency version families, pins FastAPI, and separates development dependencies into a dev group; the lockfile records the exact dependency set. Prereleases are disallowed by project configuration. Do not use an unlocked install in CI or container builds.

The committed [.python-version](../.python-version) selects `3.13.16`. Project settings require uv `0.13.8` and prevent automatic interpreter downloads; Python must be installed explicitly. The package requires Python `>=3.13.16,<3.14`, while development, containers, and planned CI use the selected patch version. Follow [environment setup](environment-setup.md) for the verified local installation commands.

Local development and containers use locked installs (`uv sync --locked`); the [CI workflow](ci.md) is configured to use the same policy, and an outdated lockfile must fail verification. The [container guide](container.md) records immutable Python/uv image digests and runtime-only installation with the selected Python patch version. Linux/amd64 container execution is verified; ARM64 deployment and hosted CI execution remain subsequent verification. Update versions, lockfile, image references, and documentation together when upgrading.

## Database and migration approach

Use synchronous SQLAlchemy with Psycopg 3 initially, with database-backed HTTP handlers declared as synchronous FastAPI handlers so blocking database work runs off the event loop. Use SQLAlchemy's connection pool and explicit transaction boundaries; avoid adding a second independent driver pool.

Alembic owns schema migrations; `create_all()` will not replace versioned migrations. Run migrations explicitly before starting the application rather than having each application worker modify the schema. Keep the migration history and database configuration within this service. Do not create tables for other services.

Domain changes and their immutable outbox records share one local database transaction. The [separate lifecycle publisher](lifecycle-outbox.md) uses existing retry columns for fenced leases; head remains `0005_registered_invitations`. It publishes after commit and checks broker confirms/routing acceptance. Broker availability alone does not determine readiness while pending events can safely persist.

Local Compose and disposable database integration tests use PostgreSQL 18.7. Local database access is bound to localhost, credentials are supplied through ignored files, and Identity has a dedicated database/user/volume. See [database setup](database-setup.md) for the verified migrations and integration commands. The Task service can retain its independently selected PostgreSQL version.

## Test approach

Use pytest with separate unit, request, and integration test directories and commands. Each suite must contain tests and return failure for test failures or an unexpectedly empty collection.

Unit tests replace external collaborators with controlled test doubles. Request tests use FastAPI/Starlette TestClient backed by HTTPX2 and inject controlled database resources; neither suite requires a Google account, another service, a broker, or a live database. Integration tests will use a disposable real PostgreSQL 18.7 instance to exercise migrations, transaction behavior, and readiness failure/recovery; SQLite is not a substitute for these tests.

See [environment setup](environment-setup.md) for installation, [application startup](application-startup.md) for startup, [database setup](database-setup.md) for migrations/integration tests, [HTTP handling](http-contract.md) for shared validation/errors/CORS, [observability](observability.md) for logging/private metrics, the [container guide](container.md) for Docker execution, and the [CI guide](ci.md) for the workflow and hosted verification steps. No passing remote pipeline is claimed yet.

Related planning documents: [Scaffold scope](scaffold-scope.md), [Contract requirements](contract-requirements.md).
