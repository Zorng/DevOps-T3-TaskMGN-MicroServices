# Identity & Workspace Scaffold Scope

This task establishes the Python/FastAPI application, service-owned PostgreSQL database and migration tooling, health endpoints, shared request/error handling, structured logging, metrics, automated tests, container setup, and CI verification.

The follow-up [Google sign-in and application-session task](google-signin.md) implements challenge/exchange/logout/current-user routes and migration `0002_google_sessions`. The [workspace task](workspace-management.md) implements list/create/read/rename/delete, initial owner memberships, shared invitation storage, the ten-owned limit, idempotency, ETags and transactional deletion events in migration `0003_workspace_management`. Invitation/member APIs, private authorization and broker delivery remain integration tasks.

Status: planning, dependency setup, configuration, application lifecycle, PostgreSQL Compose, migrations, outbox storage, health endpoints, shared HTTP handling, structured logs/private metrics, unit/request/database integration tests, the service container, and the CI workflow are implemented. See [application startup](application-startup.md), [database setup](database-setup.md), [health probes](health-probes.md), [HTTP handling](http-contract.md), [observability](observability.md), the [container guide](container.md), and the [CI guide](ci.md) for commands and verification. Hosted GitHub verification and actual registry publishing remain unverified; local checks do not establish a passing remote pipeline.

Related planning documents: [Stack decisions](stack-decisions.md), [Contract requirements](contract-requirements.md).

The [test guide](testing.md) collects suite commands, coverage, database isolation, and empty-suite failure checks in one place. The original service README is retained.
