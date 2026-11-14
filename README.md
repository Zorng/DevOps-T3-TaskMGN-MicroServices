# Task Management — DevOps Project

A collaborative task management application built as independently deployable microservices in one repository. Services may use different languages and frameworks while sharing REST and event contracts.

**Current state:** product specification, API contracts, and repository structure are available. The application services, gateway, frontend, and deployment configuration have not been implemented. Contract validation is runnable now.

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

The deployment targets are Oracle Cloud for backend infrastructure and Netlify for the frontend. Broker software, framework choices, and VM allocation remain undecided. There is no runnable Compose environment yet; orchestration will be added when component build/run commands are defined. See [infrastructure scope](infra/README.md).

Service-specific build/test/image workflows will accompany their implementations. They should react to their component paths and relevant shared contracts; contract changes require affected integrations to be checked as well. The existing workflow validates contracts only, not service code or deployment.
