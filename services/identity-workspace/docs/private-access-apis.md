# Private session introspection and workspace access

Implements both endpoints in the [internal OpenAPI](../../../api_contracts/openapi/identity-internal.openapi.json), with the [caller matrix](../../../api_contracts/authentication.md#private-service-calls), [fresh snapshot rules](../../../api_contracts/workflows.md) and [gateway boundary](../../../api_contracts/gateway-routing.md). The backend is implemented. **The gateway and deployment are not available; proof that these routes are excluded at the real public gateway remains pending.** The user confirmed that the verification command and handoff should be prepared now.

## Credentials and caller matrix

| Operation | Task key + user bearer | Notification key + user bearer |
| --- | --- | --- |
| POST `/internal/v1/auth/introspect` | 200 for a live session | 200 for a live session |
| POST `/internal/v1/workspaces/{workspace_id}/access` | 200 for a current member of an active workspace | 403 `FORBIDDEN` |

Requests are bodyless and accept no query parameters. A missing/unknown/malformed/duplicate service key receives 401 `SERVICE_CREDENTIAL_INVALID` when a syntactically valid bearer is supplied. Missing bearer is 401 `AUTH_REQUIRED`; malformed, expired, revoked or unknown bearer is 401 `SESSION_INVALID`. An allowed caller whose database check fails receives 503 `DEPENDENCY_UNAVAILABLE`, with Retry-After; it never receives an earlier cached success. Both credentials are required in one request.

Keys map to callers using fixed-size SHA-256 digest comparisons with `hmac.compare_digest`; caller-name headers are ignored. Task/Notification must have distinct high-entropy keys. Generate at least 256 random bits, keep deployment values in secret storage, and give each consumer only its own key. The registry contains digests, not plaintext keys, and has no fallback/default credential. Unset keys disable that caller. Invalid configured files, mixed env/file sources or equal keys fail startup safely.

Private requests must not forward browser Origin headers: `/internal/` rejects Origin-bearing requests and has no browser CORS/preflight access. Do not put service credentials in frontend code. `X-User-ID`, `X-User-Role`, `X-Service-Name`, forwarded identity headers and similar assertions do not override the original bearer or the key's caller identity.

## Local secret setup and startup

Use the existing Python 3.13.16/uv 0.13.8 setup and PostgreSQL 18.7. There is no new dependency, migration or application-session flow. Head remains `0005_registered_invitations`. From the service directory:

```powershell
$identityUv = Join-Path $PWD '.tools/uv/bin/uv.exe'
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
& $identityUv sync --locked
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $identityUv run --locked python scripts/setup_local_db.py
if ($LASTEXITCODE -ne 0) { throw "Database configuration setup failed." }
& $identityUv run --locked python scripts/setup_service_keys.py
if ($LASTEXITCODE -ne 0) { throw "Service-key setup failed." }
docker compose -f compose.yaml -f compose.private.yaml --profile app build identity
if ($LASTEXITCODE -ne 0) { throw "Image build failed." }
docker compose -f compose.yaml -f compose.private.yaml --profile app up --detach --no-build --wait --wait-timeout 90 identity
if ($LASTEXITCODE -ne 0) { throw "Private API startup failed." }
curl.exe -i http://127.0.0.1:8000/health/ready
```

The setup creates/preserves `.secrets/task-service-key` and `.secrets/notification-service-key` without printing values or modifying existing database/environment configuration. On POSIX, the host directory is private (`0700`) while each key is `0644`, allowing non-root container UID 10001 to read its individually bind-mounted, read-only secret; host traversal remains blocked by the directory. Windows should retain appropriate workspace/secret ACLs. Compose file secrets retain host file attributes; [uid/gid/mode overrides are not implemented for file-backed sources](https://docs.docker.com/reference/compose-file/services/#secrets).

The optional [overlay](../compose.private.yaml) mounts only the two service keys into Identity at `/run/secrets/identity_task_service_key` and `/run/secrets/identity_notification_service_key`. It does not mount database administrator credentials into Identity. Existing base Compose use remains public-only unless keys are supplied separately. The overlay gives the service the private DNS alias `identity-workspace` on this Compose project's default network. Other stacks need an explicitly shared private network from Gateway & Infrastructure; this overlay does not create cloud-wide connectivity.

For host Python, configure file paths in the shell, then start with the usual service command:

```powershell
$env:IDENTITY_TASK_SERVICE_KEY_FILE = Join-Path $PWD '.secrets/task-service-key'
$env:IDENTITY_NOTIFICATION_SERVICE_KEY_FILE = Join-Path $PWD '.secrets/notification-service-key'
& $identityUv run --locked python -m identity_workspace
```

Alternatively use `IDENTITY_TASK_SERVICE_KEY` and `IDENTITY_NOTIFICATION_SERVICE_KEY` through deployment-secret injection. Each key is 43–128 URL-safe characters. Choose an environment value **or** its corresponding `_FILE` setting, not both. Placeholders in `.env.example` are empty; no real key is committed. Readiness continues checking database health, not testing real user sessions or asserting that a caller's key is provisioned.

Keys are loaded at startup. For rotation, provision a new random key for one caller in secret storage, update that caller's configuration, and recreate the Identity container/replicas so the file mount and registry reload. Use `docker compose -f compose.yaml -f compose.private.yaml --profile app up --detach --force-recreate --wait identity` for the local overlay after changing its key file. Old keys then fail, while the other caller's key remains usable. The setup script deliberately preserves existing values and is not a rotation command. Coordinate rollout so private calls fail closed during any mismatch; never grant fallback access.

## Fresh authoritative responses

Introspection performs a fresh joined session/user query for the presented token hash, requiring no revocation and expiry later than the database clock. It returns exactly `user: {id, display_name}` and `session_expires_at`; no email, Google subject, full profile, session token or ownership count is returned. A departed user can still introspect their live session for other contexts.

Workspace access reuses membership primitives and holds the workspace SHARE lock through the snapshot transaction. It rechecks session validity after a lock wait and requires an active workspace and active actor membership. Returned fields are `actor`, `workspace_id`, `workspace_version`, `actor_membership_id`, `actor_role` and `members`, bounded to twenty current membership generations. Membership/workspace mutations take the parent write lock, so role, version and roster are consistent. Removed members/nonmembers/deleted workspaces return 404. A rejoin returns its new generation, never a prior cached one.

Authorization is fresh at its database check's snapshot. Requests checked before revocation may finish; this does not promise cancellation of in-flight cross-service work. New checks after logout/expiry/removal/deletion fail. Connection pooling, immutable key mapping and provider-key caching are not positive user-authorization caches. These endpoints never query Google or the broker. They retain closed structured logging and bounded route metrics without credential/header/query/body data.

## Task and Notification integration fixtures

Use [fixtures/private-identity.json](../fixtures/private-identity.json) for offline consumer tests. It contains shared-contract success responses plus invalid service key, missing/expired/revoked user bearer, disallowed Notification access, removed member, deleted workspace and dependency-outage cases. Timestamps/IDs are synthetic examples, not live authorization or production credentials. Inject test service keys and the user bearer separately. Copy/read fixture **data** in each consumer's tests; do not import Identity implementation code or access its database.

Task calls workspace access for every protected workspace operation and may introspect where workspace context is unavailable. Notification introspects to derive recipient ID and cannot request workspace access. Construct private headers explicitly: own `X-Service-Key`, the original `Authorization: Bearer ...`, and propagated `X-Request-ID`. Do not forward Origin or client identity assertions. A User ID supplied in a Task/Notification body is not an authentication substitute.

Use the contract's two-second private-call timeout, with at most one retry for transport/503 on these read-only POST checks. Propagate user `SESSION_INVALID` as 401 and inaccessible workspace as 404. A rejected service credential/allowlist is an operational configuration problem: consumers log a safe category and return public 503, rather than asking the user to sign in again. Never cache a positive result or authorize from stale JWT/client claims. Broker consumers use broker credentials, not an old user bearer.

## Gateway and Infrastructure handoff

The real gateway must use explicit public operation routes. Never forward `/internal/**`, health, metrics, database/broker ports or encoded/normalized aliases through public routes. Forward public Authorization unchanged; strip client-supplied `X-Service-Key`, `X-User-ID`, `X-User-Role` and equivalent assertions. The gateway itself needs no Task/Notification service key. Only backend callers receive their own keys.

Keep Identity's HTTP listener/private APIs on private networks. Local Compose's host binding is loopback-only for development; cloud deployment should not publish Identity's service port publicly. Public ingress uses HTTPS. Use TLS across hosts unless an explicitly approved encrypted private transport protects the connection; same-host private container HTTP is supported. Gateway & Infrastructure owns the actual network/firewall, proxy configuration and secret provisioning; none is claimed to be deployed by this branch.

## Real deployment verification command

Run [verify_private_boundary.py](../scripts/verify_private_boundary.py) from a trusted deployment runner able to reach both actual origins. Use a dedicated live test session for a current member and a disposable workspace. Put its token in a private ignored `.secrets/boundary-user-token` file without echoing it; do not put a token/key literal on the command line. Supply the actual gateway/private origins, not the frontend website or the direct Identity port in both positions:

```powershell
$boundaryPublic = Read-Host 'Actual public gateway origin (HTTPS)'
$boundaryPrivate = Read-Host 'Private Identity origin (HTTPS across hosts)'
$boundaryWorkspace = Read-Host 'Disposable workspace UUID with a live member'
$boundaryUserSecret = Read-Host 'Dedicated application token (hidden)' -AsSecureString
$boundaryUserPath = Join-Path $PWD '.secrets/boundary-user-token'
try {
    $boundaryToken = ([pscredential]::new('session', $boundaryUserSecret)).GetNetworkCredential().Password
    [IO.File]::WriteAllText($boundaryUserPath, $boundaryToken)
    & $identityUv run --locked python scripts/verify_private_boundary.py --public-base-url $boundaryPublic --private-base-url $boundaryPrivate --workspace-id $boundaryWorkspace --task-key-file .secrets/task-service-key --notification-key-file .secrets/notification-service-key --user-token-file $boundaryUserPath
    if ($LASTEXITCODE -ne 0) { throw "Private gateway boundary verification failed; inspect the safe report." }
} finally {
    Remove-Variable boundaryToken, boundaryUserSecret -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $boundaryUserPath -ErrorAction SilentlyContinue
}
```

The PowerShell file example is for a private Windows workspace; POSIX runners should create the token file with mode `0600` inside a private directory. HTTP public origins are accepted only for loopback verification of a real local gateway. Non-loopback private HTTP requires explicit `--same-host-private-network` and is only appropriate for the same-host private container network; it does not approve cleartext cross-host transport. Use `--ca-file` for a trusted private CA; there is no insecure TLS bypass. The tool ignores environment proxies and refuses redirects, so credentials are not silently forwarded to another origin.

The verifier first proves that Task/Notification introspection and Task access work against the private origin, that Notification access/missing credentials fail, and that authenticated public `/api/v1/me` belongs to the same user. This prevents an unrelated 404 website or an unconfigured Identity service from passing. It then requires public 404s on the actual internal POSTs, encoded/slash/dot-segment variants, GET/OPTIONS probes **with valid private credentials**, and operational health/metrics paths. Public 401/403/405 is a failure because authentication rejection is not proof of route exclusion. A missing public bearer must still fail despite forged identity assertions. Gateway header stripping also needs the gateway's own routing/configuration tests; backend ignoring assertions does not independently prove header removal.

The ignored JSON report `test-results/private-boundary.json` records origins, check labels/statuses and a UTC verification timestamp, never response bodies/headers/tokens/secret-file contents. Nonzero exit or missing report fails verification. Preserve the passing report as deployment evidence once a gateway exists. The tool's controlled-response unit tests prove its logic, not the actual deployment boundary.

**Open acceptance check:** no real public gateway exists now. Do not close the deployment-exposure criterion or describe the direct container smoke result as gateway proof. Run this command and review actual private networking/header stripping with Gateway & Infrastructure when their implementation is available.

## Automated verification

Run the README's separate unit/request/integration commands and JUnit guards. Focused checks:

```powershell
& $identityUv run --locked pytest tests/unit/test_private_api.py tests/unit/test_private_boundary_verifier.py -q
if ($LASTEXITCODE -ne 0) { throw "Private API unit tests failed." }
& $identityUv run --locked pytest tests/request/test_private_api.py -q
if ($LASTEXITCODE -ne 0) { throw "Private API request tests failed." }
& $identityUv run --locked pytest tests/integration/test_private_api.py -q
if ($LASTEXITCODE -ne 0) { throw "Private API database tests failed." }
& $identityUv run --locked python scripts/smoke_container.py --image identity-workspace:private-check
if ($LASTEXITCODE -ne 0) { throw "Private container smoke failed." }
```

CI uses synthetic keys/session fixtures and Google doubles. Database tests use isolated real PostgreSQL and cover live/expired/revoked sessions, fresh membership checks, role/generation changes, deleted workspaces, twenty-member bounds and snapshot locking. Request tests cover both routes/caller allowlists, credential/header ambiguity, body/query/CORS rejection and safe dependency failures. Docker smoke provisions only temporary secret files, runs the non-root image with the overlay, verifies direct private calls and live revocation/rejoin/outage behavior, then removes its own containers/volumes. Its report explicitly marks public-gateway verification as pending. Existing matrix/report guards gate image verification and main-only publishing; hosted CI requires the user to push/open the PR.

## Local verification recorded

Verified on 2026-11-18 (Asia/Bangkok) with unchanged locked dependencies:

- Windows: 471 unit tests, 398 request tests and 216 real PostgreSQL integration tests passed (1,085 total). All separate reports passed nonempty/zero-failure/zero-error/zero-skip guards.
- Isolated Linux Python 3.13.16/uv 0.13.8: 471 unit tests and 398 request tests passed, including POSIX secret-directory/file permissions, with report guards and dependency consistency checks. Integration was driven from Windows against disposable Linux PostgreSQL containers.
- The Linux/amd64 `identity-workspace:private-check` image built and passed complete container smoke verification. The final overlay check confirmed the private DNS alias, read-only service-key mounts as UID 10001, both caller permissions, current role/generations, revocation/deletion and no cached success during database outage/recovery. Existing public invitation/workspace/event behavior and secret-safe logging passed.
- Shared contract validation passed: 4 OpenAPI documents, 41 operations, 43 fixtures, 51 examples, 5 event variants and 14 negative cases. Consumer fixture cases separately match the internal schemas. actionlint 1.7.12, dependency consistency and local documentation links passed.
- Candidate files contained no matches for ignored local secrets; environment/key/tool/report files remained ignored. No dependency manifest/lockfile or database migration was changed.

All disposable test containers/networks/volumes were removed. Regular local containers, database data and credentials were not upgraded/restarted/changed. Nothing was committed, pushed, published or deployed. **No real public gateway was available or tested.** The boundary verifier's unit results and the direct container smoke are not deployment proof; its report explicitly records that pending scope. Hosted CI and the real gateway/network/header-stripping check remain open.
