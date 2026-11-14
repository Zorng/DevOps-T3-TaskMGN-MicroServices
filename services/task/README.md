# Task Service

Status: implementation not started; language/framework/database choice is open.

## Scope

Boards, tasks, assignments, comments, mentions, board limits, task numbering, and task cleanup.

Obtain current access through the private Identity API. Publish assignment/mention events and consume membership/workspace cleanup events. Keep membership-generation handling consistent with the workflows.

## Contracts

- [Public REST contract](../../api_contracts/openapi/task.openapi.json)
- [Authentication](../../api_contracts/authentication.md)
- [Cross-service workflows](../../api_contracts/workflows.md)
- [Event schemas and delivery](../../api_contracts/events/README.md)
- [Acceptance scenarios](../../api_contracts/acceptance-scenarios.md)

## Implementation layout

Keep this service's source, tests, dependency manifest/lockfile, database migrations, Dockerfile, and environment example in this directory. The internal layout can follow the selected framework's conventions. This directory is the service's container build context.

The service owns its database and accesses other services through contracts. Implement `/health/live` and `/health/ready`, structured logs, metrics, and the required publication/consumption reliability behavior. Build ARM64-compatible images if deployed on Oracle ARM instances.

Replace this status with the actual runtime requirements and exact install, test, migration, build, and local run commands when implementation begins. Do not present placeholder commands as runnable.
