# Identity & Workspace Contract Requirements

Reviewed against contract v1.0.0. The contract package still records owner review as pending; this checklist does not imply team sign-off. Unchecked items below are implementation and verification work, not completed features.

## Health and service boundaries

- [x] Implement `GET /health/live` as 200 with exactly `{"status":"ok"}` and no dependency checks.
- [x] Implement `GET /health/ready` as 200 with `{"status":"ok"}` when the database is usable, otherwise 503 with `{"status":"unavailable"}`. Use inexpensive, bounded probes without creating business data or performing a Google login.
- [ ] Keep health probes unauthenticated. Health, internal APIs, metrics, and database ports must not be publicly forwarded by the gateway; enforce reachability through deployment networking.
- [x] Keep broker availability separate from readiness while the outbox can safely persist pending events.
- [ ] Expose delivery degradation separately through observability when publisher/delivery behavior is implemented.
- [x] Own and migrate only the Identity database. Do not access Task or Notification tables or introduce cross-database transactions.

Sources: [public OpenAPI health operations](../../../api_contracts/openapi/identity-workspace.openapi.json), [gateway operations](../../../api_contracts/gateway-routing.md#per-service-health-semantics), and [service boundaries](../../../api_contracts/workflows.md#service-authority-and-boundaries).

## Requests, validation, and errors

- [x] Preserve valid UUID request IDs and generate one for missing/invalid IDs on direct service requests. Return `X-Request-ID` and `Cache-Control: no-store`, including on errors and health responses. The gateway also generates/replaces request IDs at the public boundary.
- [x] Use UTF-8 JSON and snake_case keys. Limit JSON bodies to 65,536 UTF-8 bytes, including requests without a trustworthy Content-Length; return 413 `PAYLOAD_TOO_LARGE` when exceeded.
- [x] Reject malformed JSON with 400 `MALFORMED_REQUEST`, unsupported media types with a body with 415 `UNSUPPORTED_MEDIA_TYPE`, and bodies on bodyless operations with 400. Do not require Content-Type when no body is present.
- [x] Supply `ContractModel` for JSON schemas: unknown properties are 422 and numeric/boolean JSON strings are not coerced. Reject unsupported query parameters with 400 `MALFORMED_REQUEST`. Future body-bearing operations must adopt the strict model and their contract-required fields/nullability.
- [x] Provide reusable Unicode code-point length validation and normalization: workspace name 1-100 and invitation email text 1-254 after trimming. Reject whitespace-only required names and never silently truncate user input.
- [ ] Add invitation email-format and registered-user lookup validation with the invitation feature; the scaffold text helper intentionally implements only normalization/bounds.
- [x] Format application errors as `{"error":{"code":"...","message":"...","request_id":"<uuid>","details":[]}}`. Keep details an array of field/reason objects; never expose stack traces, raw provider errors, credentials, or hidden-resource details. Health dependency failures retain the Health response shape above.
- [x] Handle unknown routes and unsupported methods with the shared error shape (404/405). General API dependency failures use 503 `DEPENDENCY_UNAVAILABLE` with Retry-After; rate limits use 429 `RATE_LIMITED` with Retry-After. Actual domain throttling and dependency calls arrive with their implementations.

Sources: [shared conventions](../../../api_contracts/conventions.md), the Error/Health schemas in [public OpenAPI](../../../api_contracts/openapi/identity-workspace.openapi.json), and [gateway routing](../../../api_contracts/gateway-routing.md).

## Configuration and observability

- [x] Use explicit configured frontend origins, with no wildcard production CORS. Allow Authorization, Content-Type, Idempotency-Key, If-Match, and X-Request-ID; expose ETag, X-Request-ID, and Retry-After. Recognized public-route preflight must not require a session; denied origins receive 403.
- [x] Emit structured request logs with correlation IDs and private operational metrics. Exclude Google/app tokens, Authorization, service keys, database credentials, email lookup values, and user content. Sanitize exception/validation diagnostics and lifecycle failures; keep the documented server raw access logs disabled. See [observability](observability.md).
- [ ] Supply placeholder environment configuration; keep real secrets outside source control and container build inputs.

Sources: [authentication and CORS](../../../api_contracts/authentication.md#gateway-boundary), [transport conventions](../../../api_contracts/conventions.md#transport-and-identifiers), [event observability](../../../api_contracts/events/README.md#observability), and [contribution requirements](../../../CONTRIBUTING.md#component-implementation-requirements).

## Verification

- [x] Add dedicated unit and request-level tests for each health endpoint, covering dependency-independent liveness, readiness success/failure, exact response bodies, and required headers.
- [x] Verify shared validation, limits, errors, and diagnostic redaction with unit/request tests; see [HTTP handling](http-contract.md).
- [x] Verify structured request logs, credential redaction in logging, and metrics with unit/request and real database failure/recovery tests.
- [x] Test migrations and real database readiness with disposable PostgreSQL. Verify database failure/recovery and that readiness does not depend on a running broker.
- [ ] Run separate non-empty unit, request, and database integration suites in CI; failures must block image building and any later publication. Smoke-test the built container against PostgreSQL and record results.

Sources: [testing policy](../../../CONTRIBUTING.md#endpoint-tests-and-ci-requirements) and the scaffold task acceptance criteria. The [acceptance scenarios](../../../api_contracts/acceptance-scenarios.md) also describe later business/integration coverage; they are not all scaffold deliverables.

## Constraints for subsequent child tasks

The [private API](../../../api_contracts/openapi/identity-internal.openapi.json) requires both a per-caller service key and the user's bearer token: Task and Notification may introspect; only Task may request workspace access. Sessions are opaque, not Google tokens or application JWTs; current authorization cannot fall back to stale cached access. These endpoints are deferred, not stubbed as successful operations.

Future business changes and immutable outbox records must commit in the same local transaction. Failed publication retains pending events; publication completes only after broker confirmation and routing acceptance. Preserve request/event IDs and use at-least-once delivery semantics. See [event delivery](../../../api_contracts/events/README.md#publisher-outbox). Authentication, quotas, pagination, idempotent creates, version checks, membership generations, and lifecycle behavior remain governed by the linked contracts when their child tasks are implemented.

Related planning documents: [Scaffold scope](scaffold-scope.md), [Stack decisions](stack-decisions.md).
