# Contributing

## Component boundaries

Keep each component's source, dependencies, migrations, tests, Dockerfile, and configuration examples inside its directory. Do not add a root dependency manifest that forces all services into one language. Cross-service integration uses the REST/event contracts in `api_contracts/`, never shared database tables or direct imports from another service.

Use work-area names in public documentation, examples, and PR descriptions. Personal assignments and coordination notes belong in the private coordination directory and are excluded from Git. Use synthetic identities in fixtures; never include real credentials or personal machine paths.

## Changes and review

- Follow the [branching workflow](README.md#branching-and-pull-requests): create a short-lived branch from the latest `main`, using `feat/<area>/<description>`, `fix/<area>/<description>`, `ci/<description>`, or `docs/<description>`. Use work-area names, not personal names or aliases. Backlog numbers are not required in branch names; link an existing backlog item or issue in the PR when applicable. Do not invent issue numbers or rename existing draft IDs.
- Avoid permanent personal/service branches. Cross-service changes that must land together may use one branch and PR, with review from affected work areas. Keep `main` working and do not commit task changes directly to it.
- Keep changes focused and preserve other contributors' edits. Update relevant contracts and examples when changing an interface; coordinate review across affected components before implementation diverges.
- Include tests appropriate to the behavior and documented reproduction steps. Run `make contracts` when changing contracts, examples, or their documentation links.
- Open a PR into the default branch when authorized to publish. Explain the resulting behavior and verification; use the PR template. Use draft PRs for unfinished work needing feedback.
- Require at least one other member's review and passing relevant checks before merging. Squash approved PRs and remove finished task branches. Branch protection and required-review settings must be configured separately by a repository administrator; documentation alone does not enforce them.

## Endpoint tests and CI requirements

Every implemented service endpoint must have dedicated automated tests for its method and route, including private endpoints and health probes. An endpoint is not complete until its tests and service CI check are included in the same PR.

- Unit-test endpoint handler/application logic with external collaborators replaced by controlled test doubles. Cover successful behavior and applicable validation, authorization, not-found, conflict, and dependency-failure paths; do not force irrelevant cases onto every route.
- Add request-level tests through the service's HTTP test client to verify routing, middleware, status codes, headers, and request/response shapes. These may be called endpoint, API, or integration tests depending on the framework; they complement isolated unit tests.
- Keep unit tests independent of live Google, other running services, cloud accounts, and the real message broker. Use mocks/fakes at those boundaries. Tests of database constraints, transactions, and concurrency use a disposable test database and belong in integration tests.
- Also test event publishers/consumers, retries, duplicate handling, and cleanup logic; these behaviors have no public endpoint but are still part of service correctness.
- Use the contract [acceptance scenarios](api_contracts/acceptance-scenarios.md) to select meaningful cases. Coverage of every endpoint is required; no arbitrary line-coverage percentage is prescribed.

Each backend service must supply documented, non-interactive unit-test and endpoint-test commands appropriate to its chosen stack. Its implementation PR must add CI jobs that install its locked dependencies and run those commands on pull requests and pushes to `main`. A failing test or an unexpectedly empty test suite fails CI; do not use `continue-on-error` or an empty-suite success flag to bypass this requirement.

Run checks for changed services; shared contract changes must also run affected service suites. Once those checks exist, configure required checks or a reliable aggregate gate so a skipped path-filtered job cannot silently bypass the required tests. Container publishing/deployment must depend on successful relevant test jobs. Required-check settings are configured separately in the hosting repository.

Current state: only contract-validation CI exists because no backend stack or service implementation has been added. This requirement does not claim that service unit tests already run. Add real service checks alongside implementation rather than placeholder jobs that always pass.

## Component implementation requirements

When introducing a stack, document its runtime version, package installation, local startup, tests, migrations, required environment variables, and container build/run commands. Provide a component-level `.env.example` with placeholders once variable names are known; real `.env` files remain ignored.

Backend services implement the private health endpoints and structured logging described in the contracts. Include service-owned persistence, outbox/consumer handling where applicable, and tests of permissions and failure behavior. Each component gets its own build and image; deployment of one service must not require rebuilding every other service.

Keep generated build outputs, dependency caches, database files, local secrets, and infrastructure state out of commits. Commit relevant lockfiles and source configuration. Never force-add private coordination files to make links or CI pass.
