# Infrastructure

Shared deployment configuration and the local application stack. Application source and Dockerfiles stay in their component directories (`gateway/`, `services/identity-workspace/`, `services/task/`); `infra/compose.yaml` builds them through relative paths.

Status: the **local stack** below, including the Notification service, is implemented and checked. Cloud deployment (Oracle Cloud Always Free, public HTTPS) is **not** done; see [Incomplete integrations](#incomplete-integrations). Centralized logs and metrics are configured in [monitoring/](monitoring/README.md).

## What runs locally

| Service | Role | Reachable from your Mac |
|---|---|---|
| `gateway` | Nginx public entry point | `http://127.0.0.1:8080` (only published port) |
| `identity-workspace` | Identity & Workspace API (port 8000) | no, private |
| `identity-migrate` | One-off Alembic migration job | no |
| `identity-outbox` | Identity event publisher | no |
| `identity-db` | Postgres 18.7, owned by Identity | no |
| `task` | Task API (port 8001) | no, private |
| `task-db` | Postgres 17.6, owned by Task | no |
| `notification` | Notification API and event consumer (port 8002) | no, private |
| `notification-migrate` | One-off migration job | no |
| `notification-db` | Postgres 17.6, owned by Notification | no |
| `broker` | RabbitMQ 4.1 (vhost `taskmgn`, exchange `events`) | no, private |
| `broker-init` | One-off job that creates users, queues and bindings | no |

The gateway forwards `/api/v1/notifications` to `notification:8002`. Notification checks every request with Identity and consumes `notifications.v1` from the broker.

## Prerequisites

- Docker Desktop (or Docker Engine with the Compose plugin), running.
- Python 3 (only to generate local secrets).
- `curl` and `bash` (the checks).
- Port 8080 free on `127.0.0.1`, or choose another with `GATEWAY_HTTP_PORT` in `infra/.env`.

## First start

Run from the repository root.

```bash
# 1. Create infra/.env and infra/.secrets/ with random local passwords (never overwrites).
python3 infra/scripts/setup_env.py

# 2. Check the configuration.
docker compose -f infra/compose.yaml config --quiet && echo "CONFIG VALID"

# 3. Build and start everything; waits until every service is healthy.
docker compose -f infra/compose.yaml up -d --build --wait

# 4. Look at the result.
docker compose -f infra/compose.yaml ps
curl -i http://127.0.0.1:8080/healthz
```

`identity-migrate` and `broker-init` are one-off jobs and show as `exited (0)` in `ps -a`; that is normal.

## Everyday commands

```bash
# Status
docker compose -f infra/compose.yaml ps -a

# Logs (all services, or one; Ctrl+C stops following)
docker compose -f infra/compose.yaml logs -f --tail=100
docker compose -f infra/compose.yaml logs -f task

# Restart one service
docker compose -f infra/compose.yaml restart task

# Rebuild and restart after code changes
docker compose -f infra/compose.yaml up -d --build --wait
```

## Stop and start (keeps your data)

```bash
docker compose -f infra/compose.yaml down           # stops and removes containers and networks
docker compose -f infra/compose.yaml up -d --wait   # starts again with the same data
```

`down` does **not** delete data. The databases and broker live in named volumes (`taskmgn_identity-db-data`, `taskmgn_task-db-data`, `taskmgn_notification-db-data`, `taskmgn_broker-data`) that survive `down`, `stop` and restarts.

## Deliberate data reset (destructive)

This deletes all three volumes: both databases and every queued message. Use it only when you want a clean slate.

```bash
docker compose -f infra/compose.yaml down -v
docker compose -f infra/compose.yaml up -d --build --wait
```

Keep `infra/.secrets/` and `infra/.env` through a reset unless you want new passwords; both are recreated or reused consistently because the stack starts from empty volumes.

## Configuration and secrets

- `infra/.env.example` lists every variable with placeholder values. It is the only env file in Git.
- `infra/.env` and `infra/.secrets/` hold real values, are created by `setup_env.py` and are ignored by Git. Never commit them.
- Passwords and service keys are random per machine. Task and Notification have distinct service keys; only Task may ask Identity for workspace access.
- `GATEWAY_HTTP_PORT` changes the gateway's host port (default 8080).
- `CORS_ALLOWED_ORIGINS` (gateway) and `IDENTITY_ALLOWED_ORIGINS` (Identity) default to the frontend dev origins `http://localhost:5173` and `http://127.0.0.1:5173`.
- `IDENTITY_GOOGLE_CLIENT_ID` is a public OAuth client ID, not a secret. Empty means the sign-in endpoints answer 503.

Only the gateway publishes a port. Databases and the broker publish none, so they never clash with a Postgres or RabbitMQ already running on your computer (for example Postgres on 5432).

## Private networking

| Network | Members | Notes |
|---|---|---|
| `backend` | gateway, identity-workspace, task, notification | service-to-service HTTP; Identity needs outbound access to Google |
| `identity-data` | identity-workspace, identity-migrate, identity-outbox, identity-db | internal, no outside traffic |
| `task-data` | task, task-db | internal |
| `notification-data` | notification, notification-migrate, notification-db | internal |
| `messaging` | broker, broker-init, identity-outbox, task, notification | internal; the broker has no published ports, so its management UI is private |

The gateway returns 404 for `/health/*`, `/metrics`, `/actuator/*` and `/internal/*`, so internal APIs and operational endpoints are not public. Identity cannot reach Task's database, Task cannot reach Identity's database, and the Identity API (not its outbox worker) cannot reach the broker.

Health checks follow the contract: `/health/live` shows the process is up; `/health/ready` depends on the service's own database (and, for Task, on Identity being ready). **Readiness does not depend on the broker**: events wait in each service's outbox and are published when the broker is back.

## Integration checks

Run with the stack up.

```bash
# Read-only: gateway access, private paths, health probes, network isolation.
bash infra/scripts/check.sh

# Also stops and starts things: broker outage, Identity database outage,
# service restarts, and a full down/up that must keep database and broker data.
bash infra/scripts/check.sh --lifecycle

# Event routing with the real publishers and broker.
bash infra/scripts/check-events.sh

# Same, plus a broker outage: events must stay pending, readiness must stay 200,
# and the events must arrive after the broker returns.
bash infra/scripts/check-events.sh --outage

# Deployment automation: deploy each service by digest, refuse a tag, roll back a
# deliberately broken image. Needs the stack built locally (up --build); uses a
# throwaway registry on 127.0.0.1:5055 (set REGISTRY_PORT to change it).
bash infra/scripts/check-deploy.sh
```

Each prints `PASS`/`FAIL` per check and exits 0 only if everything passed. They put back anything they stopped and remove the test events they create. The same checks run in CI (`.github/workflows/infra.yml`).

## Deploying a tested image

`infra/scripts/deploy.sh` updates **one** service from an image that already passed its tests. It is what runs on the backend host; it also works on your computer.

```bash
bash infra/scripts/deploy.sh deploy gateway ghcr.io/<owner>/<repo>/gateway@sha256:<digest>
bash infra/scripts/deploy.sh status      # recorded, previous and running image per service
bash infra/scripts/deploy.sh rollback gateway
bash infra/scripts/deploy.sh start       # start or refresh everything from the recorded images
```

Services: `gateway`, `identity-workspace`, `task`.

What it guarantees:

- **Immutable images only.** A tag such as `:main` is refused; the reference must end in `@sha256:<64 hex digits>`. An image that cannot be pulled changes nothing.
- **One service at a time.** Only that service's containers are replaced. The other services, both databases and the broker keep running and keep their data.
- **Health gate and automatic rollback.** After the swap the service must become healthy (gateway answers and still reaches Identity; Identity and Task report `/health/ready` 200). If not, the previous image is restored and the command exits non-zero (`1` = rolled back, `2` = nothing changed or the rollback itself failed and needs a person).
- **Traceable.** The recorded and previous image of each service, and a log of every deployment and rollback, are kept in `infra/.deploy/` (ignored by Git).
- **Secrets stay out of the images and out of Git.** They come from `infra/.env` and `infra/.secrets/` on the host and are never printed.

**Migration order and rollback limits.** For Identity, the migration job (`alembic upgrade head`) runs with the *new* image **before** the old Identity container is replaced; a failed migration stops the deployment and leaves the running service in place. Task applies its own Flyway migrations when it starts. Migrations are **not** reversed by a rollback: it only returns to the previous image. That is safe only while the previous version works with the migrated schema, so schema changes must be backward compatible for one release (add before you use, remove only after nothing reads it). A migration that is not compatible needs a database restore or a forward fix, not `rollback`.

On a deployment host always use `deploy.sh`, never `docker compose up --build`: a plain `up` does not know the recorded digests and would fall back to a local build.

### Publishing and deploying through GitHub

1. **Images.** On every push to `main`, after a service's own tests pass, its workflow publishes `ghcr.io/<owner>/<repo>/<service>:sha-<commit>` with the label `org.opencontainers.image.revision`. Identity and Gateway do this today. **Task's workflow does not publish yet**; its owner needs to add the same step.
2. **Run "Deploy service"** (Actions tab, manual). Choose the service and paste the image **digest** reference. The workflow first checks that the image belongs to this repository, that its source commit is on `main`, and that the service's required checks succeeded on that commit. If any check is missing or failed, nothing is deployed.
3. **Then it connects to the host** over SSH and runs `deploy.sh`.

One-time setup by the repository owner (nothing below is stored in Git):

| What | Where |
|---|---|
| Environment named `production` (optionally with required reviewers) | Settings, Environments |
| Secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` (output of `ssh-keyscan <host>`) | that environment |
| Variable `DEPLOY_PATH` (the repository checkout on the host) | that environment |
| On the host: clone the repository, run `python3 infra/scripts/setup_env.py`, and `docker login ghcr.io` with a read-only package token | the backend host |

Until the host exists (issue #32) the workflow stops at "Require the deployment target to be configured" and deploys nothing. The deployment logic itself is proven by `infra/scripts/check-deploy.sh` (see above), which runs in CI.

## Sign-in and session checks (manual)

These need a Google client ID, so they are not automated.

1. Ask the teammate who owns the Google setup for the client ID, and make sure `http://localhost:5173` is an authorized JavaScript origin for it.
2. Put it in `infra/.env` as `IDENTITY_GOOGLE_CLIENT_ID=<client id>`, then apply it: `docker compose -f infra/compose.yaml up -d --wait`.
3. Run the frontend on `http://localhost:5173` pointing at `http://127.0.0.1:8080`, and sign in with Google.
4. Check through the gateway, using the access token the sign-in returns:

```bash
TOKEN=<access_token>
curl -i -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/api/v1/me                    # 200, your profile
curl -i -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/api/v1/auth/logout   # session ends
curl -i -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/api/v1/me                    # 401 after logout
```

Creating a workspace and opening its Task boards with a token exercises Task's call to Identity for membership.

## Incomplete integrations

Recorded explicitly, as the issue requires. Nothing here is faked.

| Integration | State | Effect |
|---|---|---|
| Task's cleanup consumer | Not enabled in Compose | `task-cleanup.v1` events reach the queue (checked) but are not acted on. Notification consumes `notifications.v1` (checked). |
| Notification image deployment | Not wired | `deploy.sh` replaces the gateway, Identity and Task by digest. Notification uses the local build (or `NOTIFICATION_IMAGE`) until it is added there. |
| Sign-in and session/workspace checks | Manual | Need a real Google client ID and a browser sign-in. Not run in CI. |
| Events created through public APIs | Not automated | `check-events.sh` writes events into the outboxes with the contract's example payloads, because creating them through the APIs needs a Google sign-in. |
| Deployment to the Oracle VM | Automation written and proven locally; not run against the VM | The VM does not exist yet (issue #32). The SSH step of `deploy.yml` is untested until then. |
| Task image publishing | Not done | Task's workflow does not publish to the registry, so Task cannot be deployed through GitHub yet (the script works with any digest). |
| Public HTTPS, certificates | Not started | The local stack only. |
| Centralized logs and metrics | Configured, not deployed | Configuration and checks are in [monitoring/](monitoring/README.md); running it next to the live stack is #34. |

## Troubleshooting

- `identity-migrate` fails with `password authentication failed for user identity_app`: the Identity database was first created without its app role. Run the deliberate reset above. (`identity-db` copies Identity's init script through `infra/identity-db/init-wrapper.sh` so it also runs on Docker Desktop for Mac.)
- `port is already allocated` on 8080: set `GATEWAY_HTTP_PORT` in `infra/.env` and run `up -d --wait` again.
- Build fails with `no such host`: a temporary network problem; run the build again.

## Broker

Broker configuration, users, queues, retry/dead-letter behavior, backup and restore are documented in [broker/README.md](broker/README.md). The stack above uses the same configuration files with no published ports.

## Deployment requirements (still open for the cloud)

- Separate service-owned databases and persistent storage, even when sharing one VM.
- Private service/database/broker networking and a public HTTPS gateway.
- Durable event delivery with the queue/retry/dead-letter behavior in the [event contract](../api_contracts/events/README.md).
- Centralized logs and metrics with request/event correlation.
- Environment-based secrets, backups/restore steps, health checks and independently deployable images.
- ARM64-compatible images if Oracle ARM VMs are used.

For Identity's private checks, follow the [network/secret handoff](../services/identity-workspace/docs/private-access-apis.md#gateway-and-infrastructure-handoff). Keep Identity ports private and run its boundary verifier from a trusted runner after the real gateway is deployed; that cloud criterion remains open.