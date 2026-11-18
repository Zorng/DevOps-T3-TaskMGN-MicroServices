# Identity lifecycle outbox demo and consumer handoff

Date: 2026-11-18. These filenames were agreed with the user. Producer/broker checks below are implemented and reproducible. Owning-consumer integration remains pending because Notification and Task lifecycle consumers are not implemented yet; the user requested this handoff rather than placeholder consumer claims.

## Reproduce the completed producer demonstration

From `services/identity-workspace/`, using the installed uv path from its README and a running Docker Desktop:

```powershell
& $identityUv sync --locked
if ($LASTEXITCODE -ne 0) { throw "Install failed." }
& $identityUv run --locked pytest tests/integration/test_outbox.py -q --junitxml=test-results/outbox-integration.xml
if ($LASTEXITCODE -ne 0) { throw "Producer integration failed." }
& $identityUv run --locked python scripts/verify_test_report.py test-results/outbox-integration.xml
if ($LASTEXITCODE -ne 0) { throw "Invalid producer report." }
& $identityUv run --locked python scripts/smoke_container.py --image identity-workspace:outbox-check
if ($LASTEXITCODE -ne 0) { throw "Container demonstration failed." }
```

The suite creates uniquely named, disposable PostgreSQL/RabbitMQ projects and generates test credentials privately. The smoke script uses only standard-library host Python and the built runtime image. It never connects to a developer database or real Google account.

Show the JUnit and smoke reports, without showing secret files. Expected evidence:

1. Real authenticated invitation, removal, leave and deletion mutate authoritative state and persist matching events while no broker runs. Removed/deleted access already returns 404.
2. The separate non-root worker retries the absent broker; no sent mark appears, and HTTP readiness stays 200 while PostgreSQL is writable.
3. Start the test broker; the same worker recovers automatically. Eight events are positively confirmed, five reach `notifications.v1` and three reach `task-cleanup.v1`; pending count becomes zero. These are queue counts, not consumer effects.
4. The integration suite deliberately removes an event's route; RabbitMQ returns the mandatory message and the row stays pending. Restoring topology permits successful retry.
5. The suite deliberately leaves the outbox unmarked after a positive confirm, expires the abandoned lease and starts a new worker. The queue receives duplicate messages with identical ID and bytes.
6. A broker restart preserves an already confirmed persistent message. Test projects are removed, and the worker exits cleanly on SIGTERM.

See the [technical report](identity-lifecycle-outbox-report.md) and [operator guide](../services/identity-workspace/docs/lifecycle-outbox.md) for exact results, configuration and troubleshooting. Run all service suites before proposing merge; the focused suite supplements the complete regression checks.

## Consumer fixtures and ownership

[identity-lifecycle-events.json](../services/identity-workspace/fixtures/identity-lifecycle-events.json) contains the three canonical Identity event variants plus ordered duplicate/delayed scenarios. Fixtures use synthetic IDs and actor names, contain no credentials/emails and are validated in the producer unit suite. Preserve fixture message IDs when replaying. They are handoff data, not a mock implementation of Notification or Task.

| Owner | Required integration behavior/evidence |
| --- | --- |
| Notification | Transactionally commit processed-event marker and one notification per event/recipient; acknowledge only after commit; historical delayed invitation stays generic and target access is rechecked |
| Task | Transactionally commit processed marker, membership-generation/workspace tombstone and effect; acknowledge only after commit; cleanup matches exact membership ID and prevents deleted-resource resurrection |
| Infrastructure | Real durable routes, consumer keys/ACLs, TLS/private networking, delayed retries, confirmed dead-letter forwarding/replay, queue depth/age alerts and deployment identifiers |
| Identity | Keep local success independent of broker; stable immutable events, retries, confirms, request/event correlation, and reproducible producer reports |

## Required owning-consumer demo when implementations arrive

Use a dedicated integration environment with each service's own database and real application consumers. Seed synthetic registered owner/recipient accounts through test-only fixtures; create invitations and memberships through public APIs. Never share another service's database or inject trusted client identity headers. Do not consume the same logical queue with the delivery probe during this run.

1. **Invitation outage and duplicate:** stop/delay Notification, create an invitation and show successful Identity response/pending delivery. Restore publication/consumer processing. Query Notification's authenticated public API: exactly one recipient notification with `read_at=null`. Repeat the identical broker event after a consumer restart; still one notification. Answer/cancel the invitation before delayed delivery and show that opening its target performs the current authorization/state check.
2. **Removal/rejoin generation:** create tasks assigned to membership M1; remove/leave and hold the removal event. Rejoin through a new invitation to create M2, then create a new-generation assignment. Deliver the old M1 event twice. Task clears only M1 assignments, persists the M1 tombstone and preserves M2 assignment. Show that authoritative access was denied between removal and rejoin even before cleanup arrived.
3. **Workspace deletion and delayed order:** delay cleanup, delete the workspace successfully in Identity and immediately show 404 authority. Deliver `workspace.deleted.v1`, an older removal event and the deletion duplicate. Task's public API remains inaccessible; terminal workspace tombstone prevents an in-flight old mutation from resurrecting data. Existing generic notifications stay historical.
4. **Consumer crash windows:** stop processing before its transaction commits, then replay; show marker/effect rollback. Stop after commit before broker acknowledgement, then replay; show one committed effect. Preserve original message IDs throughout.
5. **Consumer failure/dead-letter recovery:** inject a transient failure with the contract's delayed retries; do not hot-loop. After the defined failed attempts, confirm durable dead-letter forwarding. Repair and replay the original ID; verify one effect and preserved dedup/tombstones. If forwarding cannot be confirmed, the original message must remain retained.

Record each owner's commit/image, broker deployment/topology, UTC run time, scenario IDs (E01–E10/O01 where applicable), sanitized public response assertions, correlation UUIDs, duplicate-suppression counters and final pending/dead-letter state. Attach actual consumer test/report locations and the PR Actions URL. Mark this checklist complete only after those observations exist.

Current status: producer and broker demonstration passed locally; consumer-effect demonstration and hosted CI results await their actual implementations/runs.
