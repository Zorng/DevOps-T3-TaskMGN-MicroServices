# Message broker

Status: broker configuration, provisioning, operations runbook and operational tests in place (issue #30). Service owners implement their own outboxes, publishers, consumers and deduplication; this folder provides the broker and the rules those clients must follow.

The broker is **RabbitMQ** (official `rabbitmq:4.1-management-alpine` image, multi-arch: amd64 and arm64). It delivers the events defined in [`api_contracts/events/README.md`](../../api_contracts/events/README.md). It is private: only services on the private network, and operators on the host, can reach it.

Why RabbitMQ: the contract needs durable queues, confirmed publication, detection of unroutable messages, per-queue dead-letter stores, delayed retries and per-service credentials. RabbitMQ provides all of these natively (quorum queues, publisher confirms with `mandatory`, dead-letter exchanges, TTL queues, topic permissions) and has mature Python and Java clients. Kafka and Redis streams have no per-message dead-letter or delay without extra code.

## Topology

Virtual host `taskmgn`. Everything below is durable and defined in `definitions.json`.

```
producers ──> events (topic) ──┬─ invitation.created.v1          ─┐
 identity                      ├─ task.assigned.v1                ├─> notifications.v1 ──> notification
 task                          ├─ task.comment_mentioned.v1      ─┘
                               ├─ workspace.member_removed.v1    ─┐
                               └─ workspace.deleted.v1            ├─> task-cleanup.v1  ──> task
                                                                 ─┘
consumer failure ──> retry (direct) ──> <queue>.retry.<delay> ──(TTL expires)──> retry-return ──> <queue>
consumer gives up / message rejected ──> dead-letter (direct) ──> <queue>.dlq   (kept until an operator acts)
```

| Object | Type | Purpose |
| --- | --- | --- |
| `notifications.v1`, `task-cleanup.v1` | quorum queue | Logical consumer queues. Rejected messages are dead-lettered with at-least-once guarantees: the message stays here until the dead-letter queue confirms it. |
| `<queue>.dlq` | quorum queue | Dead-letter store. No TTL, no length limit: nothing is discarded automatically. |
| `<queue>.retry.5s`, `.30s`, `.2m`, `.10m`, `.30m` | classic queue with TTL | Delay queues. After the TTL a message returns to the main queue through `retry-return`. |
| `events` | topic exchange | Producers publish here, routing key = event type. |
| `retry`, `retry-return`, `dead-letter` | direct exchanges | Retry delays, return to the main queue, dead-letter store. |

The five event routes and the queue each goes to are checked against the event contract by `tests/test_topology.py`: if the contract changes, CI fails until the broker configuration matches.

## Start, stop and reset

Run from the repository root. Needs Docker with Compose. Python 3.13 is needed only for the tests.

```sh
# 1. Create your local secrets file (Git ignores it). Replace the placeholders.
cp infra/broker/.env.example infra/broker/.env

# 2. Start the broker and wait until it is healthy
docker compose -f infra/broker/compose.yaml up -d --wait broker

# 3. Apply the topology, then create the service users (idempotent: re-run any time)
docker compose -f infra/broker/compose.yaml --profile init run --rm broker-init

# Logs
docker compose -f infra/broker/compose.yaml logs -f broker

# Stop (data is kept in the named volume taskmgn-broker-data)
docker compose -f infra/broker/compose.yaml down

# Deliberate reset: delete ALL broker data, then start again from step 2
docker compose -f infra/broker/compose.yaml down -v
```

Order matters: step 3 creates and binds every route **before** the service users exist, so no producer can publish against an unprovisioned broker. Start the services only after step 3.

Host ports are bound to `127.0.0.1` only: AMQP `5672`, management `15672`, metrics `15692`. Change them with `BROKER_AMQP_PORT`, `BROKER_MGMT_PORT` and `BROKER_METRICS_PORT` in `.env` if they clash with another stack. In deployment, publish no broker port at all; services reach it as `broker:5672` on the private network.

## Connection requirements for services

| Setting | Value |
| --- | --- |
| Host / port | `broker` / `5672` on the private Docker network (`127.0.0.1` / `BROKER_AMQP_PORT` from the host) |
| Virtual host | `taskmgn` |
| Username | the service name: `identity`, `task` or `notification` |
| Password | a secret from the environment (suggested variables: `BROKER_HOST`, `BROKER_PORT`, `BROKER_VHOST`, `BROKER_USERNAME`, `BROKER_PASSWORD`); never commit it |
| Transport | plain AMQP inside the private network; require TLS if the broker is ever reached across networks |

What each account may do (least privilege, enforced by the broker):

| User | Publish | Consume | Notes |
| --- | --- | --- | --- |
| `identity` | `events`: `invitation.created.v1`, `workspace.member_removed.v1`, `workspace.deleted.v1` only | nothing | |
| `task` | `events`: `task.assigned.v1`, `task.comment_mentioned.v1` only; `retry`, `dead-letter` | `task-cleanup.v1` | |
| `notification` | `retry`, `dead-letter` | `notifications.v1` | |
| `broker-admin` | everything | everything | Operators only. Never give it to a service. |

Services cannot declare, change or delete queues and exchanges. **Turn off automatic topology declaration** in your client library (for example, Spring AMQP's `RabbitAdmin` auto-startup) or the connection will be closed with `ACCESS_REFUSED`.

### Publishers (outbox relay)

1. Publish to exchange `events` with routing key = the event type (for example `task.assigned.v1`).
2. Enable **publisher confirms** and set **`mandatory=true`**. An unroutable message is returned by the broker (`basic.return`); treat that as a failure.
3. Mark an outbox record as published **only after** the broker confirmed the publication and no return was received. On any failure, keep the record pending and retry with the **same event ID and payload**.
4. Set `delivery_mode=2` (persistent), `content_type=application/json`, `message_id` = the event ID and `type` = the event type. The body is the contract's event envelope.

### Consumers

1. Use **manual acknowledgement** and a small prefetch (for example 10). Acknowledge only after the business effect and the deduplication record (event ID) are committed. If the consumer dies before acknowledging, the broker redelivers the message, so processing must be idempotent.
2. **Malformed or unsupported message:** reject it with `requeue=false`. The broker moves it to `<queue>.dlq`. There is no retry.
3. **Temporary failure (attempt *n*, counted from 1):** republish the message to exchange `retry` (persistent, confirmed, same `message_id` and body) with header `x-attempt: n+1`, then acknowledge the original. Never requeue immediately: that would loop hot.

   | Failed attempt | Routing key on `retry` | Delay |
   | --- | --- | --- |
   | 1 | `<queue>.retry.5s` | ~5 s |
   | 2 | `<queue>.retry.30s` | ~30 s |
   | 3 | `<queue>.retry.2m` | ~2 min |
   | 4 | `<queue>.retry.10m` | ~10 min |
   | 5 | `<queue>.retry.30m` | ~30 min |
   | 6 | publish to exchange `dead-letter`, routing key `<queue>` | held for an operator |

4. If a republish to `retry` or `dead-letter` is **not confirmed**, do not acknowledge the original: leave it so the broker redelivers it. A message is never dropped unconfirmed.

## Dead letters: inspect and replay

Dead-lettered messages wait in `<queue>.dlq` until an operator acts. Inspect depth with the metrics below or the management UI on `http://127.0.0.1:15672` (admin account). To replay, fix the cause first, then:

```sh
export BROKER_ADMIN_USER=... BROKER_ADMIN_PASSWORD=...   # from infra/broker/.env
python3 -m venv .venv && .venv/bin/pip install -r infra/broker/tests/requirements.txt

.venv/bin/python infra/broker/scripts/replay_dead_letters.py notifications.v1 --dry-run
.venv/bin/python infra/broker/scripts/replay_dead_letters.py notifications.v1 --limit 10
```

Each message returns to the main queue with the **same event ID**, body and headers (broker death headers are removed), and leaves the dead-letter queue only after the broker confirms it is back. Consumer deduplication on event ID makes a replay safe.

## Health and metrics

| What | How |
| --- | --- |
| Liveness (Compose health check) | `rabbitmq-diagnostics -q check_running` and `check_local_alarms` |
| Health via API | `GET http://127.0.0.1:15672/api/health/checks/alarms` (admin account) |
| Metrics for the observability stack | Prometheus endpoint `http://127.0.0.1:15692/metrics` (private network only) |
| Queue depth | `rabbitmq_queue_messages_ready{queue="notifications.v1"}`, and the same for `task-cleanup.v1`, every `.dlq` and every `.retry.*` queue |
| Alert on | any `.dlq` depth above 0; `notifications.v1` or `task-cleanup.v1` depth growing; broker alarms (memory or disk) |

Dependency on the broker: a broker outage must not fail the services' readiness while their outboxes remain writable. Messages wait in each service's outbox until publication is confirmed.

## Credentials

Nothing secret is in Git. Admin credentials come from `RABBITMQ_DEFAULT_USER`/`PASS` (set from `.env`); service users are created by `scripts/provision.py` from the `BROKER_*_PASSWORD` variables. Use long random secrets outside local development (`openssl rand -base64 24`).

**Rotate a service password:**

1. Edit that service's `BROKER_<SERVICE>_PASSWORD` in `.env` (or your secret store).
2. Re-run `docker compose -f infra/broker/compose.yaml --profile init run --rm broker-init`. It sets the new password; the topology is untouched.
3. Update the secret the service reads and restart that service. Existing connections stay valid until they close; to force it:
   `docker compose -f infra/broker/compose.yaml exec broker rabbitmqctl close_all_user_connections <service> "password rotated"`.

**Rotate the admin password:**
`docker compose -f infra/broker/compose.yaml exec broker rabbitmqctl change_password "$BROKER_ADMIN_USER" '<new password>'`, then update `BROKER_ADMIN_PASSWORD` in `.env`.

## Backup, restart and recovery

- **Restart:** `docker compose -f infra/broker/compose.yaml restart broker`. Queues, bindings, users and persistent messages survive (proved by `test_messages_and_routes_survive_a_broker_restart`). Unacknowledged messages are redelivered. Never change the `hostname: broker` line in `compose.yaml`: RabbitMQ keys its data on it.
- **Backup:** `infra/broker/scripts/backup.sh [dir]` exports the topology (`definitions-*.json`) and a copy of the data volume (`data-*.tgz`), stopping the broker briefly for a consistent copy. Treat the archive as a secret: it contains user credentials. Backups are written to `infra/broker/backups/`, which Git ignores.
- **Restore:** `infra/broker/scripts/restore.sh backups/data-<stamp>.tgz` replaces the volume contents.
- **Verify after any restore, restart or credential change:**
  ```sh
  .venv/bin/python -m pytest infra/broker/tests -v
  ```
  (this clears the queues, so run it on a non-production broker or before services go live).
- **Rebuild from nothing:** `down -v`, then the three start steps above. The topology comes from `definitions.json`; queued messages are lost, which is why services keep their own outboxes.

## Tests

| File | Needs a broker | Proves |
| --- | --- | --- |
| `tests/test_topology.py` | no | Definitions match the event contract; retry delays; dead-letter stores never discard; permission table; each event type has exactly one allowed producer |
| `tests/test_broker.py` | yes | Confirmed and routable publication for all five routes; unroutable messages are detected; least privilege; restart persistence; redelivery of unacknowledged messages; delayed retry; dead-lettering; replay keeping the event ID; health and queue-depth metrics |

```sh
python3 -m venv .venv && .venv/bin/pip install -r infra/broker/tests/requirements.txt
.venv/bin/python -m pytest infra/broker/tests/test_topology.py -v    # offline
# with the broker started and provisioned (steps 1 to 3 above):
.venv/bin/python -m pytest infra/broker/tests -v
```

CI (`.github/workflows/broker.yml`) runs the offline checks and validates the Compose file (`config` job), then starts the broker, provisions it and runs every test (`operations` job). Tests read `infra/broker/.env` if it exists.

## Files

| File | Purpose |
| --- | --- |
| `compose.yaml` | Broker service, one-shot `broker-init` job, named data volume |
| `definitions.json` | Exchanges, queues and bindings (no users, no secrets) |
| `rabbitmq.conf`, `enabled_plugins` | Broker settings; management and Prometheus plugins |
| `scripts/provision.py` | Applies the topology, then creates users and permissions from environment secrets |
| `scripts/replay_dead_letters.py` | Operator tool for dead-letter replay |
| `scripts/backup.sh`, `scripts/restore.sh` | Backup and restore |
| `.env.example` | Placeholder environment (copy to `.env`) |
| `tests/` | Offline and operational tests |

Not in scope here: provisioning the Oracle VM and its network rules (see the infrastructure scope in `infra/`), and each service's own outbox, publisher and consumer code.
