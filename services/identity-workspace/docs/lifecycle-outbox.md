# Identity lifecycle outbox

Identity owns producer reliability for `invitation.created.v1`, `workspace.member_removed.v1` and `workspace.deleted.v1`. Follow the [shared event rules](../../../api_contracts/events/README.md), [workflows](../../../api_contracts/workflows.md) and [acceptance scenarios](../../../api_contracts/acceptance-scenarios.md). Notification owns notification effects/deduplication; Task owns membership/workspace tombstones and cleanup. Queue delivery is distinct from successful consumer processing.

## Runtime and adapter decisions

Existing Python 3.13.16, SQLAlchemy/Psycopg/PostgreSQL and pytest remain unchanged. Pika 1.4.4 is pinned for its established blocking-confirm API. `jsonschema[format]` moves from development-only to runtime at its existing locked version, so production validates the complete shared schema. The exact canonical JSON Schema is bundled under the package for independent image builds; a unit test rejects drift against `api_contracts/events/event.schema.json`. Existing runtime versions are preserved in `uv.lock`.

RabbitMQ 4.4.1 Alpine is digest-pinned in [compose.outbox.yaml](../compose.outbox.yaml) as an opt-in reference/test adapter. This does not select the team's shared/cloud broker. [Pika's mandatory/confirm example](https://pika.readthedocs.io/en/stable/examples/blocking_publish_mandatory.html), [RabbitMQ confirms](https://www.rabbitmq.com/docs/confirms) and [reliability guidance](https://www.rabbitmq.com/docs/reliability) explain why persistent delivery, durable queues, positive confirmation and unroutable detection are all necessary. A broker-neutral publisher boundary lets another adapter provide the same guarantees.

## Atomic producers and immutable wire messages

The domain transaction validates and inserts its event alongside invitation creation, removal/leave or workspace deletion. A validation, outbox insert or commit failure rolls the entire mutation back. Successful idempotent retries and repeat terminal operations append no extra event. Existing database triggers protect event ID/type/payload/creation time. Events contain only the contract projection: IDs and the actor's bounded display-name snapshot. No email lookup value, Google identity, session token or copied workspace content enters the event.

| Event | Aggregate/version | Logical durable queue |
| --- | --- | --- |
| `invitation.created.v1` | Invitation ID / 1 | `notifications.v1` |
| `workspace.member_removed.v1` | Immutable membership ID / 2; reason `removed` or `left` | `task-cleanup.v1` |
| `workspace.deleted.v1` | Workspace ID / version after deletion | `task-cleanup.v1` |

The worker revalidates committed payloads, including aggregate semantics, and encodes deterministic sorted JSON with a 64 KiB limit. Every retry preserves the event ID and identical encoded body. Malformed legacy rows stay pending with a sanitized `invalid_event` diagnostic; repair the producer/data through a reviewed database operation, never mutate an immutable payload or discard a pending event to clear an alarm.

## Lease, confirmation and crash recovery

1. A short transaction locks one due row using `FOR UPDATE SKIP LOCKED`, increments `attempts`, and advances `next_attempt_at` by 120 seconds. That timestamp is the lease; no new migration is needed.
2. Commit the claim before connecting to the broker. No domain transaction or pooled database connection waits for network delivery.
3. Send a persistent JSON message with stable `message_id`, request correlation ID and exact event-type routing key. Enable publisher confirms and `mandatory` routing. A NACK, returned unroutable message, timeout or disconnected broker is a failure.
4. After positive confirmation/routing acceptance, mark `published_at` using an atomic event-ID/attempt comparison. An expired worker cannot mark or reschedule a newer worker's claim.
5. On failure, keep the row pending and schedule exponential delay starting at one second, with up to 20% positive jitter and a hard 60-second cap. Retry indefinitely. On a crash or database failure, the committed lease expires and another worker resumes. No manual reset is required.

A confirm followed by a crash before the sent update can duplicate publication. This is intentional at-least-once delivery. No global or contiguous sequence is promised. A slow/stale worker can also overlap after lease expiry; fencing protects database state, while stable IDs enable consumer deduplication. Pika uses one connection per attempt, heartbeat 5s, socket timeout 3s, connection-stack timeout 8s, blocked-connection timeout 5s and a 10s channel/publish deadline. Closing is bounded separately. These are below the 120s lease; choose throughput optimizations only after profiling.

Confirmed rows are retained at least seven days from confirmation. Pending rows are never purged. Run one bounded cleanup batch in the configured container:

```powershell
docker compose -f compose.yaml -f compose.outbox.yaml --profile outbox exec -T outbox python -m identity_workspace.outbox.worker --purge-confirmed
```

The command deletes at most 1,000 eligible rows. Schedule repeated batches through Infrastructure if needed. No automatic retention job is silently started.

## Configuration and local operation

Follow the [README setup](../README.md#lifecycle-publisher-setup). The generated files `.secrets/broker-url`, `.secrets/broker-probe-url` and `.secrets/broker-definitions.json` are ignored. Setup preserves existing complete files and never rotates credentials behind a broker database. A partial setup fails; restore the missing file from the matching secret source rather than replacing credentials on a persistent broker. On POSIX the containing directory is 0700; individually mounted files are readable by the non-root container. Restrict directory traversal/Windows ACLs; do not print or commit these files.

| Variable | Meaning |
| --- | --- |
| `IDENTITY_BROKER_URL` | Optional secret `amqp(s)://user:password@host:port/vhost`; percent-encode credentials/vhost |
| `IDENTITY_BROKER_URL_FILE` | Preferred mounted file containing the URL; mutually exclusive with inline URL |
| `IDENTITY_BROKER_EXCHANGE` | Preprovisioned exchange, default `identity.lifecycle.v1` |
| `IDENTITY_BROKER_SECRETS_DIR` | Compose-only directory for generated broker files, default `.secrets` |
| `IDENTITY_BROKER_PORT` | Local loopback AMQP port, default 5673; not a public gateway route |

Only the worker requires broker configuration. The HTTP process neither opens a broker connection nor requires the broker secret file. Production requires `amqps`, hostname verification and trusted CA validation using the system SSL context; URL query parameters cannot disable TLS or override controlled timeouts. Infrastructure provides certificates/trusted CA configuration separately. Do not use local plaintext AMQP across an untrusted network.

For host Python with an appropriate private broker URL/file and database settings:

```powershell
& $identityUv run --locked python -m identity_workspace.outbox.worker
# Process at most one due event; a failed delivery exits 1, invalid configuration exits 2.
& $identityUv run --locked python -m identity_workspace.outbox.worker --once
```

`--once` exits 0 when no event is due; use the pending count/age to distinguish idle from fully drained. SIGTERM/SIGINT stops after the bounded current attempt. Do not enable the image's HTTP health probe for the separate worker; the overlay disables it.

## Broker topology and deployment handoff

Local setup imports topology before the worker sends messages: vhost `identity`; durable topic exchange `identity.lifecycle.v1`; durable quorum queues `notifications.v1` and `task-cleanup.v1`; exact event bindings; a durable dead-letter exchange and separate durable `.dead` quorum queue per consumer queue. At-least-once quorum dead-lettering and reject-publish overflow are configured. No TTL silently expires events. The single-node test broker demonstrates persistence/restart, not production HA or capacity.

The generated producer has no configure or consume permission, only exchange publishing plus a topic-key allowlist for the three Identity types. The separate local delivery-probe identity can consume the logical test queues; it is not an application consumer. The worker receives only its producer URL, not topology/probe credentials. Keep these credentials separate from HTTP service keys. Consumers and Infrastructure must agree the shared broker topology, TLS/network restrictions, per-service ACLs, queue capacity/backups, retry queues and alert routing before deployment. A binding mismatch leaves publications pending; do not disable mandatory routing to silence failures.

Task and Notification must implement the contract's transactional dedup/effect/ack rules, bounded delayed retries and confirmed dead-letter forwarding/replay. The reference topology does not implement their application processing or retry loops. Multiple consumer replicas compete on each logical queue; do not create per-replica queues.

## Monitoring and recovery

Worker metrics use a separate private listener: host mode `127.0.0.1:8001`; Compose binds inside the private container network and publishes no host metrics port. Read via:

```powershell
docker compose -f compose.yaml -f compose.outbox.yaml --profile outbox exec -T outbox python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/metrics', timeout=2).read().decode())"
docker compose -f compose.yaml -f compose.outbox.yaml --profile outbox logs --tail 50 outbox
```

Metrics: `identity_outbox_pending`, `identity_outbox_oldest_pending_age_seconds`, `identity_outbox_published_total`, `identity_outbox_publish_failures_total{reason}`, `identity_outbox_retries_total`, `identity_outbox_storage_available` and log-sink failures. Alert when oldest pending age exceeds 300 seconds, storage is unavailable, or failures persist; the worker also emits `outbox_backlog_old`. Count includes leased rows. On DB failure the last pending gauges are stale; evaluate them alongside storage availability. Counters reset at process restart, while durable attempts/pending state stay in PostgreSQL. No IDs or user content become metric labels.

Closed structured logs carry event/request UUID, allowlisted type, attempt and safe failure category. Raw Pika diagnostics, connection URLs, provider errors and event bodies are suppressed. Troubleshoot using `broker_unavailable` (connection/TLS/credentials/resource block), `unroutable` (topology), `nack` (broker refusal), `invalid_event` (contract/legacy producer) or `storage_unavailable`. Inspect provisioning/network/secret sources privately; do not paste URLs or payloads into tickets.

Restore the broker or bindings and leave the worker running: due rows recover automatically. Restarting the worker also recovers after outstanding leases expire. Never reset `published_at`, generate replacement event IDs or delete pending rows as an outage workaround.

## Reproducible producer checks

```powershell
& $identityUv run --locked pytest tests/unit/test_outbox.py tests/request/test_outbox_boundary.py -q
& $identityUv run --locked pytest tests/integration/test_outbox.py -q --junitxml=test-results/outbox-integration.xml
if ($LASTEXITCODE -ne 0) { throw "Outbox checks failed." }
& $identityUv run --locked python scripts/verify_test_report.py test-results/outbox-integration.xml
& $identityUv run --locked python scripts/smoke_container.py --image identity-workspace:outbox-check
```

Run the complete suites from the README as well. Integration uses unique disposable PostgreSQL/broker projects, generated secrets, and real confirms/permissions/routing/restart checks. Tests deterministically expire leases instead of sleeping two minutes. Existing endpoint tests verify invitation creation, removal, leave and deletion insert their event and roll back on failure. Smoke starts the real worker without a broker, verifies HTTP readiness, restores the broker, then verifies eight delayed events and zero pending rows. A separate one-off probe receives messages from both disposable queues and matches five unique invitation event IDs and three unique cleanup IDs against the committed outbox, including exact canonical payloads, persistent delivery properties and request correlation. It waits up to 30 seconds for receipts and allows identical duplicate deliveries. Missing IDs, wrong routes, changed payloads or incorrect headers still fail. The report records unique/delivery/duplicate counts; it does not infer success from a one-time queue-counter snapshot.

Only the disposable probe receives the generated broker-probe credential file through an extra read-only mount; the running publisher retains only its producer credential. The probe acknowledges validated messages, so these disposable queues are drained by verification. It is a delivery check, not a Notification/Task implementation, and makes no consumer-effect claim. Never run it against shared application queues. Unit regression tests cover delayed and duplicate receipts, missing events masked by duplicates, incorrect routes/properties and sanitized failures.

Every smoke run intentionally republishes one committed invitation event with the same ID and bytes before receiving messages. It requires the probe to observe that duplicate as well as all eight unique events. This deterministically tests the confirm-before-mark redelivery outcome instead of relying on a timing accident to produce a duplicate.

See the [dated technical evidence](../../../docs/identity-lifecycle-outbox-report.md) and [owning-consumer demonstration checklist](../../../docs/identity-lifecycle-outbox-demo.md). Hosted CI must still pass on the user's PR; local verification does not claim a GitHub Actions run.
