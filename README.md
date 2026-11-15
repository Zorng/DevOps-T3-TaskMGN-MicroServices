# Task Management — DevOps Project

A collaborative task management application built as independently deployable microservices in one repository. Services may use different languages and frameworks while sharing REST and event contracts.

**Current state:** product specification, API contracts, and repository structure are available. The [Task service scaffold](services/task/README.md) uses Java/Spring Boot and PostgreSQL with health probes, migrations, tests, and CI. Business APIs, other services, gateway, frontend, and cloud deployment remain unimplemented.

## Repository layout

```text
services/
  identity-workspace/   Google sign-in, users, workspaces, memberships, invitations
  task/                Boards, tasks, assignments, comments, mentions
  notification/        In-app notifications and read state
gateway/               Public API routing and ingress
frontend/              Web application
infra/                 Local orchestration, cloud configuration, observability
api_contracts/         OpenAPI, event schemas, fixtures, validation
docs/                  Product and technical documentation
.github/workflows/     Continuous integration
```

Each backend directory is its own build context and will contain its own dependencies, database migrations, tests, and Dockerfile. Sharing a repository does not permit cross-service database access or imports of another service's internal implementation.

Start with the [product specification](docs/product-specification.md), [API contract guide](api_contracts/README.md), and [contribution guide](CONTRIBUTING.md). Component READMEs describe their technical scope and implementation expectations.

## Branching and pull requests

Use short-lived branches from the latest `main`, with one focused change per branch. Name branches by change type and work area:

```text
feat/task/create-task
feat/identity-workspace/google-signin
feat/notification/consume-assignment
feat/gateway/service-routing
feat/frontend/workspace-page
fix/task/assignment-validation
ci/contracts-validation
docs/api-contracts
```

Use `feat/<area>/<description>` or `fix/<area>/<description>` for component changes, and `ci/<description>` or `docs/<description>` for shared changes. Use lowercase, hyphen-separated descriptions and work-area names, not personal names or aliases. A backlog number is not required in the branch name.

1. Branch from the latest `main` once the repository has its initial commit.
2. Implement a focused change with relevant tests and documentation.
3. Open a PR into `main`, linking the backlog item or existing issue when one exists. Use a draft PR for unfinished work needing feedback.
4. Obtain approval from at least one other member and pass the relevant CI checks.
5. Squash merge the approved PR, then delete the branch.

Keep `main` working; do not commit task changes directly to it. Avoid permanent branches per person or service. Changes spanning services may share one branch and PR when they must land together, such as an event schema and its producer/consumer updates. Include review from affected work areas.

Review requirements are team policy; repository branch protection must be configured separately. See [CONTRIBUTING.md](CONTRIBUTING.md) for implementation and review guidance.

## Validate contracts locally

Python 3.13 and GNU Make are used for contract tooling; this does not prescribe the backend languages.

```sh
make setup
make contracts
```

`make setup` creates `.venv/` and installs the pinned validator dependencies. Alternatively:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r api_contracts/tools/requirements.txt
.venv/bin/python api_contracts/tools/validate_contracts.py
```

The GitHub Actions contract workflow runs the same validator on pull requests, pushes to `main`, and manual dispatch. It needs no deployment secrets. It becomes active when this repository is hosted on GitHub with Actions enabled.

## Application development

Implement each component in its own directory using the agreed contracts. Add exact install, test, build, and run commands to its README when choosing its stack. Commit dependency lockfiles where the selected tooling supports them.

Frontend requests go through the gateway. Backend services use REST for synchronous access checks and broker-delivered events for notifications and cleanup. Each service owns its data and publishes events through its own transactional outbox.

The deployment targets are Oracle Cloud for backend infrastructure and Netlify for the frontend. The Task stack is selected; broker software, the remaining component frameworks, and VM allocation are undecided. A Task-only local Compose setup is available in `services/task/`; full-application orchestration will follow the other components. See [infrastructure scope](infra/README.md).

Service-specific build/test/image workflows will accompany their implementations. They should react to their component paths and relevant shared contracts; contract changes require affected integrations to be checked as well. The contract workflow validates shared contracts; the Task workflow runs service tests and builds its container. Image publishing and deployment remain future work.

## Testing requirements

Every implemented service endpoint must have dedicated unit tests for its handler/application behavior and request-level tests for its HTTP contract. Cover success and relevant failure cases, including permissions and validation. Private endpoints, health probes, and event-processing behavior also need appropriate tests.

Each service adds its unit-test and endpoint-test CI jobs with its implementation. Relevant tests must pass before merging or publishing/deploying images. Shared contract changes trigger affected service checks. See [the testing and CI policy](CONTRIBUTING.md#endpoint-tests-and-ci-requirements).

Task unit, endpoint, and PostgreSQL integration tests are available now; see [Task service commands](services/task/README.md#tests-and-ci). Other service test commands will follow their selected stacks.
