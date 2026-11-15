# Identity & Workspace Service Container

The [Dockerfile](../Dockerfile) packages FastAPI and its Alembic migrations. [Compose](../compose.yaml) runs the application alongside its service-owned PostgreSQL database. Verified tooling is Docker Engine 29.9.2 and Compose v5.6.1, using Docker Desktop's Linux engine.

## Build and runtime choices

| Input | Pin |
| --- | --- |
| Python builder/runtime | `python:3.13.16-slim-bookworm@sha256:73aa5faee130c60da030e105ec971ccf57473fbd22ce73bd6fc9ba99a40b838d` |
| Build-time uv | `ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85` |
| Dependencies | [uv.lock](../uv.lock), installed with `uv sync --locked --no-dev --no-editable` |
| PostgreSQL | Digest-pinned 18.7 image in [Compose](../compose.yaml) |

The multi-stage build follows [uv's Docker guidance](https://docs.astral.sh/uv/guides/integration/docker/). The final image contains the installed application, runtime dependencies, and migration files. It runs as UID/GID 10001, with one Uvicorn worker on port 8000. uv, pytest, HTTPX2, host virtual environments, and build caches are absent. The [.dockerignore](../.dockerignore) allows only required build inputs, excluding credentials and generated files before they reach the builder.

Compose supplies a read-only root filesystem, a small temporary filesystem, dropped Linux capabilities, and no privilege escalation for the application/migration containers. Raw access logs and proxy-header trust are disabled; application request logs use the safe JSON format in [observability](observability.md). The Docker healthcheck calls `/health/live` using Python's standard library. Readiness separately checks database/outbox availability, so `compose up --wait` does not replace an explicit readiness request.

Verified platform: `linux/amd64`. ARM64 execution remains unverified. Update versions, image digests, the lockfile, and documentation together when upgrading. Locked inputs reproduce dependency selection; generated build timestamps/provenance can still change an image ID.

## Generate local configuration

Run commands from `services/identity-workspace/`, after [environment setup](environment-setup.md), with Docker Desktop running:

```powershell
$identityUv = Join-Path $PWD '.tools/uv/bin/uv.exe'
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
& $identityUv run --locked python scripts/setup_local_db.py
if ($LASTEXITCODE -ne 0) { throw "Local configuration setup failed." }
```

Setup preserves existing credentials/configuration and creates missing ignored files:

| File | Purpose |
| --- | --- |
| `.secrets/database-owner-password` | PostgreSQL bootstrap password |
| `.secrets/database-app-password` | Application-role password |
| `.env` | Host Python configuration using `localhost:5434` |
| `.env.container` | Container configuration using Compose DNS `db:5432` |

Both environment files use the generated app-role password. Compose supplies `.env.container` through its runtime `env_file`; credentials are never build arguments or image contents. No password values are printed. [.env.example](../.env.example) contains placeholders only.

Edit `.env.container` for container-specific origins/logging/settings. `.env` configures host Python separately. Existing files are preserved, so verify manually customized URLs still match the intended database. The app file is optional when Compose loads the model so database-only commands/tests remain usable; starting the app without a valid URL fails configuration validation.

## Build and start

```powershell
docker compose --profile app config --quiet
if ($LASTEXITCODE -ne 0) { throw "Invalid Compose configuration." }
docker compose --profile app build identity
if ($LASTEXITCODE -ne 0) { throw "Service image build failed." }
docker compose --profile app up --detach --no-build --wait --wait-timeout 90 identity
if ($LASTEXITCODE -ne 0) { throw "Container startup failed." }
docker compose --profile app ps --all
```

The default local tag is `identity-workspace:local`. A direct build is also available: `docker build --tag identity-workspace:local .`. Neither command publishes an image.

Startup uses [Compose dependency conditions](https://docs.docker.com/compose/how-tos/startup-order/): PostgreSQL becomes healthy, the one-off `migrate` container successfully runs `alembic upgrade head`, then `identity` starts. A failed migration blocks application startup. Application workers never run migrations. The migration container should show exit code 0; application/database containers should be healthy.

The application binds to `127.0.0.1:8000`, and PostgreSQL to `127.0.0.1:5434`. If a host Uvicorn process occupies port 8000, stop it or set `$env:IDENTITY_HTTP_PORT = '8001'` before startup and use that port for requests. `IDENTITY_HTTP_PORT`, `IDENTITY_IMAGE`, and `IDENTITY_CONTAINER_ENV_FILE` are Compose-only shell overrides; keep them outside strict app environment files. Database-only commands such as `docker compose up --detach --wait db` still work without the `app` profile.

Use `config --quiet` to validate without printing expanded runtime environment values. Read service logs with `docker compose --profile app logs --tail 50 identity`.

## Verify health and migrations

```powershell
curl.exe -i http://127.0.0.1:8000/health/live
curl.exe -i http://127.0.0.1:8000/health/ready
curl.exe http://127.0.0.1:8000/metrics
```

Both health probes should return 200 with `{"status":"ok"}`, X-Request-ID, and Cache-Control: no-store. Metrics use Prometheus text. These private routes remain outside public gateway routing. During a database outage, live/metrics remain available while readiness returns 503; no broker is required by the scaffold.

To run migrations explicitly against an already running Compose database:

```powershell
docker compose --profile app run --rm --no-deps migrate
if ($LASTEXITCODE -ne 0) { throw "Container migration failed." }
```

For later image/schema updates, stop the application, rebuild, run migrations successfully, and start it again. Repeating the initial migration is safe.

## Automated smoke test

```powershell
& $identityUv run --locked python scripts/smoke_container.py
if ($LASTEXITCODE -ne 0) { throw "Container smoke test failed." }
```

The [smoke script](../scripts/smoke_container.py) builds the image and creates a unique `identity-smoke-*` Compose project with temporary credentials and free localhost ports. It checks fresh/repeated migrations, non-root execution, Python version, runtime-only packages, private-file exclusion, read-only storage, real HTTP responses/headers/metrics, database outage/recovery without application recreation, DEBUG log redaction, and completed SIGTERM cleanup. It removes only its disposable containers, network, volume, and temporary files afterward. The development database and native PostgreSQL installation remain separate.

The script uses the Python standard library and Docker CLI, returns nonzero on failure, and records status, image identity/platform, and completed checks in ignored `test-results/container-smoke.json`. Use `--skip-build` for an already built image and `--image <tag>` to select another local tag.

Uvicorn can re-raise SIGTERM after finishing lifespan cleanup. The smoke test accepts exit 143 only with completed application/server shutdown logs; forced termination, OOM, and cleanup errors fail verification. See [Uvicorn signal handling](https://github.com/Kludex/uvicorn/blob/main/uvicorn/server.py) and [Docker stop behavior](https://docs.docker.com/reference/cli/docker/container/stop/).

## Stop locally

Stop only the application, keeping PostgreSQL running:

```powershell
docker compose --profile app stop --timeout 20 identity
```

Stop/remove the development project's containers/network while preserving its database volume:

```powershell
docker compose --profile app down
```

## Verification recorded for Step 11

Verified on 2026-11-15 with Docker Desktop's Linux/amd64 engine:

- The image built using the pinned Python/uv images and locked runtime dependencies.
- The automated container smoke test passed, including database outage/recovery, credential-safe DEBUG output, and completed shutdown cleanup (Uvicorn's expected SIGTERM exit 143).
- Unit suite: 140 passed; request suite: 80 passed; PostgreSQL integration suite: 27 passed. All 247 tests passed with zero failures, errors, or skips.
- The local application and PostgreSQL containers started successfully, with migrations completed before application startup. Existing host configuration and database passwords were preserved.
- Disposable smoke/integration resources were cleaned up. The development application/database are left running for local use.

The original README is retained. The [CI guide](ci.md) documents Step 12's test/build/publishing gates and hosted verification steps; local results do not establish a passing remote pipeline.

Related documents: [test guide](testing.md), [database setup](database-setup.md), [application startup](application-startup.md), and [stack decisions](stack-decisions.md).
