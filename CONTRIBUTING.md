# Contributing

## Component boundaries

Keep each component's source, dependencies, migrations, tests, Dockerfile, and configuration examples inside its directory. Do not add a root dependency manifest that forces all services into one language. Cross-service integration uses the REST/event contracts in `api_contracts/`, never shared database tables or direct imports from another service.

Use work-area names in public documentation, examples, and PR descriptions. Personal assignments and coordination notes belong in the private coordination directory and are excluded from Git. Use synthetic identities in fixtures; never include real credentials or personal machine paths.

## Changes and review

- Work from the current default branch once the repository has an initial commit. Use `backlog/<approved-id>` for an approved backlog item; do not invent issue numbers or rename existing draft IDs.
- Keep changes focused and preserve other contributors' edits. Update relevant contracts and examples when changing an interface; coordinate review across affected components before implementation diverges.
- Include tests appropriate to the behavior and documented reproduction steps. Run `make contracts` when changing contracts, examples, or their documentation links.
- Open a PR into the default branch when authorized to publish. Explain the resulting behavior and verification; use the PR template. Use draft PRs for unfinished work needing feedback.
- Require at least one other member's review and passing relevant checks before merging. Squash approved PRs and remove finished task branches. Branch protection and required-review settings must be configured separately by a repository administrator; documentation alone does not enforce them.

## Component implementation requirements

When introducing a stack, document its runtime version, package installation, local startup, tests, migrations, required environment variables, and container build/run commands. Provide a component-level `.env.example` with placeholders once variable names are known; real `.env` files remain ignored.

Backend services implement the private health endpoints and structured logging described in the contracts. Include service-owned persistence, outbox/consumer handling where applicable, and tests of permissions and failure behavior. Each component gets its own build and image; deployment of one service must not require rebuilding every other service.

Keep generated build outputs, dependency caches, database files, local secrets, and infrastructure state out of commits. Commit relevant lockfiles and source configuration. Never force-add private coordination files to make links or CI pass.
