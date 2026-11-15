# Notification Service

Status: implementation not started; language/framework/database choice is open.

## Scope

Recipient-specific in-app notifications, unread counts, read state, and notification-event deduplication.

Introspect sessions through the private Identity API. Consume notification events transactionally and acknowledge only after commit. There is no public notification creation endpoint.

## Contracts

- [Public REST contract](../../api_contracts/openapi/notification.openapi.json)
- [Authentication](../../api_contracts/authentication.md)
- [Cross-service workflows](../../api_contracts/workflows.md)
- [Event schemas and delivery](../../api_contracts/events/README.md)
- [Acceptance scenarios](../../api_contracts/acceptance-scenarios.md)

## Implementation layout

Keep this service's source, tests, dependency manifest/lockfile, database migrations, Dockerfile, and environment example in this directory. The internal layout can follow the selected framework's conventions. This directory is the service's container build context.

The service owns its database and accesses other services through contracts. Implement `/health/live` and `/health/ready`, structured logs, metrics, and the required publication/consumption reliability behavior. Build ARM64-compatible images if deployed on Oracle ARM instances.

Replace this status with the actual runtime requirements and exact install, test, migration, build, and local run commands when implementation begins. Do not present placeholder commands as runnable.

## Required tests and CI

Every implemented endpoint needs dedicated unit tests and request-level HTTP tests covering successful behavior and applicable failure cases. Include private routes/health probes and event-processing tests where applicable. Mock external collaborators in unit tests; verify persistence and transaction behavior separately with a disposable test database.

Add documented, non-interactive test commands and real CI jobs in the same PR as the service implementation. Test failures and unexpectedly empty suites must fail CI. Follow the shared [testing and CI policy](../../CONTRIBUTING.md#endpoint-tests-and-ci-requirements). Framework/test-runner selection remains open.
