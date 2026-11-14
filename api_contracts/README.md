# Task Management API Contracts

Version: 1.0.0  
Status: Complete initial design for team review; not yet signed off by all owners  
Date: 2026-11-14  
Product baseline: [specification v1.1](../docs/product-specification.md)

This package defines the implementation boundary for the three services, API gateway, and frontend. It contains REST contracts and broker-neutral event contracts. New technical decisions below are design choices for review, not previously approved product changes. No service implementation or infrastructure is provisioned here.

## Read in this order

1. [Endpoint inventory](endpoint-inventory.md): routes and work areas.
2. [Shared conventions](conventions.md): errors, validation, pagination, concurrency, and request retries.
3. [Authentication](authentication.md): Google sign-in, application sessions, and private authorization calls.
4. [Workflows](workflows.md): service responsibilities and failure behavior.
5. [Event delivery](events/README.md): payload ownership, retries, deduplication, and cleanup.
6. [Gateway routing](gateway-routing.md): Gateway & Infrastructure's public/private routing boundary.
7. [Acceptance scenarios](acceptance-scenarios.md): behavior to verify during implementation.

## Machine-readable contracts

| File | Work area | Consumers |
| --- | --- | --- |
| [Identity & Workspace OpenAPI](openapi/identity-workspace.openapi.json) | Identity & Workspace | Frontend, Gateway & Infrastructure |
| [Task OpenAPI](openapi/task.openapi.json) | Task | Frontend, Gateway & Infrastructure |
| [Notification OpenAPI](openapi/notification.openapi.json) | Notification | Frontend, Gateway & Infrastructure |
| [Private Identity OpenAPI](openapi/identity-internal.openapi.json) | Identity & Workspace | Task, Notification |
| [Event JSON Schema](events/event.schema.json) | Notification coordinates; producer owners maintain variants | Task, Identity & Workspace, Notification, Gateway & Infrastructure |

REST files use OpenAPI 3.1.1 in JSON (valid OpenAPI, importable into OpenAPI tools). Each file is self-contained. Event payloads use JSON Schema draft 2020-12. The schema's `.example` identifier is a logical identifier, not a hosted service.

The three public specs describe disjoint gateway paths. Private identity calls and per-service health routes bypass the public gateway. There is no public notification creation endpoint and no public event-ingestion endpoint. The broker and gateway do not own business databases.

## Concrete design choices for review

- Opaque bearer application sessions issued by Identity after Google ID-token verification. Eight-hour fixed expiry, immediate server-side revocation, no refresh token. Frontend holds the token in memory and obtains a new sign-in after reload/expiry.
- Private calls use a distinct per-service key plus the user's bearer token. Task obtains a current workspace/member snapshot for every request; Notification introspects the session on every request. Authorization snapshots are not cached.
- UUID identifiers, snake_case JSON, UTC timestamps, date-only start/due dates, and strict input fields.
- `If-Match` protects workspace, board, and task updates/deletes. Create operations use `Idempotency-Key` with 24-hour retention.
- Exact-email invitation and lookup are one owner-only operation. No user-directory endpoint.
- Mention ranges refer to Unicode code-point offsets, alongside stable user IDs. At most 50 ranges per comment and 20 distinct users.
- REST waits for the local database commit, not notification delivery. Producers use transactional outboxes and consumers use transactional deduplication.
- Each workspace join has a new immutable membership ID; removal events target that generation so late cleanup cannot clear new assignments after rejoining.
- No automatic default boards, mandatory polling, or drag-and-drop protocol. Frontend creates named boards through the ordinary API.

Broker software, service languages/frameworks, database engines, production hostnames, credentials, and Oracle VM allocation remain deployment/implementation choices. Event semantics and API payloads do not depend on those choices. A broker supporting durable queues, publisher confirmations, acknowledgements, retries, and dead-letter handling is required.

## Examples and validation

- [Schema fixtures](examples/schema-fixtures.json): synthetic request/response objects used by mocks and validation.
- [Event examples](examples/events.json): one valid event per type.
- [Demo HTTP requests](examples/demo.http): a frontend/backend integration walkthrough with placeholder sessions.
- Every request-body schema and successful JSON response includes an example in its OpenAPI document. Examples illustrate shapes; they are not a running mock server or an executable Google login.

Run from this directory:

```sh
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
.venv/bin/python tools/validate_contracts.py
```

The validator checks OpenAPI structure, local references, route/operation uniqueness, fixtures, event variants, example coverage, and selected negative cases. It does not prove authorization, concurrency, retry behavior, or a real implementation; those are covered by the implementation acceptance scenarios. See [validation results](validation-results.md).

## Review by work area

| Work area | Required review |
| --- | --- |
| Identity & Workspace | Google exchange/session policy; identity/private APIs; invitations; membership generations; Identity-produced events |
| Task | Task APIs; validation; task numbering/versioning; access checks; task events and cleanup consumers |
| Notification | Notification API; event envelope/payloads; deduplication; retries and monitoring |
| Gateway & Infrastructure | Routing; private-network isolation; origin policy; service credentials; broker/deployment feasibility |
| Frontend | Sign-in UX; all frontend payloads; errors; mention offsets; pagination and mock examples |

All owners review before implementation is treated as contract-frozen. A change affecting another component needs its owner's review. During v1, do not silently add request/response/event fields: the schemas are deliberately strict. Update the contract version, fixtures, and consumers together. A breaking public API change requires a new URL major version; a breaking event change requires a new event type version and migration plan.

## References

- [OpenAPI 3.1.1 specification](https://spec.openapis.org/oas/v3.1.1.html)
- [JSON Schema draft 2020-12 validation](https://json-schema.org/draft/2020-12/json-schema-validation)
- [Google ID token verification](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token)
- [Google Identity Services JavaScript reference](https://developers.google.com/identity/gsi/web/reference/js-reference)
