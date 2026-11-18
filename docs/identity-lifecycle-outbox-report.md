# Identity lifecycle outbox technical report

Date: 2026-11-18. Scope: Identity producer reliability and the agreed consumer handoff. Service branch: `feat/identity-workspace/lifecycle-outbox`. The implementation is local; the user handles commit, push and PR creation.

## Result and remaining acceptance

The producer writes immutable, schema-validated lifecycle events with domain changes and publishes them through a separate worker using durable delivery, mandatory routing and positive confirms. Failed/unroutable events remain pending; fenced database leases recover abandoned attempts. Stable event IDs/payloads support duplicate delivery. No synchronous broker dependency was added to domain endpoints or readiness.

The real RabbitMQ reference adapter and isolated PostgreSQL checks demonstrate producer reliability. **Notification has no consumer implementation in this checkout; Task has no lifecycle cleanup consumer.** The user confirmed none is available on another branch and requested a handoff. Actual invitation-notification effects, generation-safe assignment cleanup and workspace cleanup with those owning consumers remain an open integration gate. Broker receipt is not evidence of completed consumer processing. The [demo checklist](identity-lifecycle-outbox-demo.md) identifies the remaining checks.

## Implementation and decisions

| Area | Implementation |
| --- | --- |
| Atomic producers | Shared append/validation helper called inside existing invitation creation, removal/leave and deletion transactions |
| Contract | Bundled canonical JSON Schema, semantic aggregate checks, deterministic encoding, 64 KiB cap; no email/token fields |
| Claims/recovery | Existing `attempts` and `next_attempt_at` implement committed 120s leases; PostgreSQL `SKIP LOCKED`; attempt comparison fences stale workers |
| Broker adapter | Pika 1.4.4; persistent mandatory publications, confirms, safe timeouts; RabbitMQ 4.4.1 Alpine digest pinned for local/reference tests |
| Retry/retention | Exponential retry from 1s with jitter, capped at 60s; pending never discarded; bounded manual purge only after at least 7 confirmed days |
| Runtime | Same non-root/read-only service image for HTTP and separate worker; broker secret mounted only into worker |
| Observability | Closed event/request-ID logs, bounded failure categories, private metrics for pending age/count, retries, confirms and storage availability |
| Migrations | No new schema objects required; head remains `0005_registered_invitations`; historical event payloads remain immutable |
| CI | Existing unit/request/integration matrix now includes producer tests; real broker and image-worker smoke gates existing image publishing |

See [service instructions](../services/identity-workspace/docs/lifecycle-outbox.md), [source](../services/identity-workspace/src/identity_workspace/outbox/), [reference topology generator](../services/identity-workspace/scripts/setup_broker.py) and [workflow](../.github/workflows/identity-workspace.yml). Shared/cloud broker choice, production TLS/network/ACLs/HA, capacity/backups and alert routing remain Infrastructure decisions. This reference broker provisions durable logical queues/dead-letter stores and producer type permissions; it does not implement consumers' application retries or effects.

## Verification evidence

Executed with locked Python 3.13.16/uv 0.13.8 on Windows, driving real Linux PostgreSQL 18.7 and RabbitMQ 4.4.1 containers:

| Check | Observed result |
| --- | --- |
| Unit suite | 524 passed |
| Request suite | 404 passed |
| PostgreSQL/broker integration suite | 228 passed |
| Nonempty JUnit guards | Passed with zero failures, errors or skips |
| Total Windows regression checks | 1,156 passed (524 unit + 404 request + 228 integration) |
| Isolated Linux Python 3.13.16 | 524 unit + 404 request passed; both report guards and locked dependency consistency passed |
| Focused real-broker suite | 12 passed; also included in the complete integration suite |
| Linux/amd64 service image | `identity-workspace:outbox-check` built and complete smoke passed |
| Outage/recovery image smoke | Worker initially fails safely without broker; HTTP readiness stays 200; all 8 lifecycle events then confirmed; 5 notification / 3 cleanup queue messages; pending 0 |
| Other image smoke | Fresh/repeated migrations, public/private role/session boundaries, immediate revocation, generation semantics, non-root/read-only worker, database outage/recovery, sanitized logs/metrics, clean shutdown |

Real-broker tests cover distinct concurrent claims, stale completion fencing, rollback, retry after a new worker, retention boundaries, confirmed persistent delivery, unroutable returns, broker outage/recovery, confirmed-message persistence across restart, producer ACL rejection, and the confirm-before-mark crash window. That crash test receives two identical messages with the original ID and bytes. A public HTTP lifecycle scenario commits invitation/removal/deletion events, denies revoked/deleted access immediately, and publishes only afterward. Existing domain suites separately cover leave and atomic rollback/idempotent mutations.

Generated machine evidence stays ignored under `services/identity-workspace/test-results/`: `unit.xml`, `request.xml`, `integration.xml`, `outbox-integration.xml`, and `container-smoke.json`. CI uploads suite/smoke reports and fails missing, empty, failed or skipped results. No real Google, broker or cloud credentials are needed. All verification projects have unique names and remove only their own containers/networks/volumes. Regular development containers/data are preserved.

Shared contract validation passed: 4 OpenAPI documents, 41 operations, 43 fixtures, 51 examples, 5 event variants, 14 negative cases and 33 public operations. actionlint 1.7.12 accepted the Identity workflow; locked dependency consistency and local Markdown targets passed. The lockfile adds only Pika and moves the existing jsonschema dependency into runtime; existing versions remain unchanged. Git candidate files were checked for ignored local credential values; secret/config/report/tool paths remain ignored. The container image ID recorded in the ignored smoke report is `sha256:0f239d09deeb98cc29f372e776731a0551bff01433dee6b737d69ea0806458c3` (Linux/amd64).

Integration tests were driven from Windows against actual Linux database/broker containers; Linux unit/request execution does not claim a hosted Ubuntu integration run. Hosted GitHub Actions execution, registry publication, cloud deployment, ARM64 execution and the previously pending public-gateway isolation check are not claimed by this report.

## Review/demo handoff

- Review atomicity and event aggregate semantics with Identity owners.
- Infrastructure reviews adapter/topology/TLS/secret separation and binds both logical queues before enabling producers.
- Notification and Task use the [synthetic fixture](../services/identity-workspace/fixtures/identity-lifecycle-events.json) and [demo sequence](identity-lifecycle-outbox-demo.md) to supply owning-consumer integration evidence.
- Attach the real PR Actions run and final consumer run identifiers to this report when available. Keep secrets and protected payloads out of recordings/log attachments.
