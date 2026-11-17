# Workspace management and ownership quota

This feature implements the five workspace operations in the [public Identity OpenAPI](../../../api_contracts/openapi/identity-workspace.openapi.json), using [shared conventions](../../../api_contracts/conventions.md), [application sessions](../../../api_contracts/authentication.md), and [lifecycle workflows](../../../api_contracts/workflows.md). It introduces no new runtime dependency, credential, environment variable, or Google flow. The locked development-only JSON Schema validator checks actual projections/events against the shared contracts.

## Setup and upgrade

Follow the [service README](../README.md#setup-and-local-verification) for Python/uv, private environment files, PostgreSQL, and Google sign-in. Python 3.13.16 and PostgreSQL 18.7 remain supported. From the service directory:

```powershell
$identityUv = Join-Path $PWD '.tools/uv/bin/uv.exe'
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
& $identityUv sync --locked
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
docker compose --profile app build identity
if ($LASTEXITCODE -ne 0) { throw "Image build failed." }
docker compose --profile app up --detach --no-build --wait --wait-timeout 90 identity
if ($LASTEXITCODE -ne 0) { throw "Container startup failed." }
curl.exe -i http://127.0.0.1:8000/health/ready
```

The migration container applies `0003_workspace_management` after the existing authentication migration, followed by the [membership generation guard](membership-permissions.md) in `0004_membership_generations`. It preserves users, sessions, and pending outbox records. Host Python users instead run `uv run --locked alembic upgrade head` before starting the server. Readiness checks the current migration and required workspace storage privileges. A broker or Task outage does not block authoritative local deletion or readiness while the outbox can persist.

## HTTP examples

Use an eight-hour application `access_token` returned by Google exchange, not a Google ID token. The browser sign-in helper intentionally keeps its token in page memory and does not display it. For API testing, capture the exchange response in your API client's private local variable, or use the examples below with an application token you already obtained. Never save real tokens in a collection, tracked file, command history, URL, screenshot, or CI log.

The following Windows PowerShell commands prompt without echoing the token, retain it only in the current process, and work with Windows PowerShell 5.1 as well as PowerShell 7. Use a temporary workspace name because the last example deletes the workspace it creates.

```powershell
$identityApi = 'http://127.0.0.1:8000'
$identitySecureToken = Read-Host 'Application access_token (hidden)' -AsSecureString
$identityToken = ([pscredential]::new('session', $identitySecureToken)).GetNetworkCredential().Password
$identityBearer = @{ Authorization = "Bearer $identityToken" }
$identityCreateKey = [guid]::NewGuid().ToString()
$identityCreateHeaders = @{ Authorization = "Bearer $identityToken"; 'Idempotency-Key' = $identityCreateKey }
$identityCreateBody = @{ name = ' Temporary verification workspace ' } | ConvertTo-Json -Compress

# 201: new workspace, active owner membership, version 1, ETag "1".
$identityCreated = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$identityApi/api/v1/workspaces" -Headers $identityCreateHeaders -ContentType 'application/json' -Body $identityCreateBody
$identityWorkspace = $identityCreated.Content | ConvertFrom-Json
$identityWorkspace
$identityCreated.Headers['ETag']

# 201: identical original response and ETag; no additional workspace/quota use.
$identityRetried = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$identityApi/api/v1/workspaces" -Headers $identityCreateHeaders -ContentType 'application/json' -Body $identityCreateBody
$identityRetried.Content

# 200: active owned and joined workspaces, newest first. Preserve next_cursor as opaque.
$identityPage = Invoke-RestMethod -Method Get -Uri "$identityApi/api/v1/workspaces?limit=1" -Headers $identityBearer
$identityPage
if ($null -ne $identityPage.next_cursor) {
    $identityCursor = [uri]::EscapeDataString($identityPage.next_cursor)
    Invoke-RestMethod -Method Get -Uri "$identityApi/api/v1/workspaces?limit=20&cursor=$identityCursor" -Headers $identityBearer
}

# 200: fetch current state and ETag before changing it.
$identityTarget = "$identityApi/api/v1/workspaces/$($identityWorkspace.id)"
$identityCurrent = Invoke-WebRequest -UseBasicParsing -Method Get -Uri $identityTarget -Headers $identityBearer
$identityMutationHeaders = @{ Authorization = "Bearer $identityToken"; 'If-Match' = $identityCurrent.Headers['ETag'] }
$identityRenamed = Invoke-WebRequest -UseBasicParsing -Method Patch -Uri $identityTarget -Headers $identityMutationHeaders -ContentType 'application/json' -Body '{"name":"Renamed verification workspace"}'
$identityRenamed.Content

# 204, empty body: delete using the updated ETag, then confirm /me quota decreased.
$identityMutationHeaders['If-Match'] = $identityRenamed.Headers['ETag']
$identityDeleted = Invoke-WebRequest -UseBasicParsing -Method Delete -Uri $identityTarget -Headers $identityMutationHeaders
$identityDeleted.StatusCode
Invoke-RestMethod -Method Get -Uri "$identityApi/api/v1/me" -Headers $identityBearer

# Clear bearer values from this shell when finished.
Remove-Variable identityToken, identitySecureToken, identityBearer, identityCreateHeaders, identityMutationHeaders
```

Keep `$identityCreateKey` and the same request body if a create times out or returns an ambiguous 500/503; retry that operation before starting another. Each intentional new create needs a fresh UUID key. For the same key, a changed normalized name returns 409 `IDEMPOTENCY_CONFLICT`. Create retries return their original body/ETag even after rename; GET returns current state. Retrying a create after the workspace becomes inaccessible returns 404, including a changed body with that key. After 25 hours the key can create a new resource, so reconcile older uncertain actions first.

After successful deletion, GET, PATCH, DELETE, and the original keyed create retry return 404. A current member who is not the owner gets 403 on rename/delete; a stranger gets 404. Missing `If-Match` returns 428; stale exact ETags return 412; weak, wildcard, multiple, unquoted, zero, or malformed values return 400. Fetch current state before reconciling a stale mutation. Sending the same trimmed name with a current ETag is a no-op and preserves version/timestamps.

Names are 1–100 Unicode code points after surrounding whitespace is trimmed. Blank, oversized, wrong-type, extra-property, or empty PATCH bodies return 422. Duplicate workspace names are permitted. Invalid/duplicate list parameters return 400. Lists sort `created_at DESC, id DESC` with default limit 20, maximum 100. The limit may change between pages. Lists recheck active membership on each page and do not promise a historical snapshot.

## Persistence and concurrency

`0003_workspace_management` adds:

| Table | Responsibility |
| --- | --- |
| `workspaces` | UUID, owner, trimmed name, version, timestamps and deletion tombstone |
| `memberships` | Immutable membership generation ID, role, joined/revoked timestamps; partial unique indexes for active membership/owner |
| `invitations` | Shared invitation ID, workspace, inviter, recipient, status and timestamps; one pending invitation per recipient/workspace |
| `idempotency_records` | User/method/canonical-path/key scope, normalized SHA-256 fingerprint, resource ID and original successful status/body/ETag |
| `workspace_cursors` | Hash of a random opaque cursor, actor, fixed ordering/scope, last sort tuple and expiry |

Creation locks the user's row with PostgreSQL `FOR NO KEY UPDATE`, counts active workspaces owned by that user, checks fewer than ten, then inserts the workspace, initial owner membership and retry record and synchronizes `/me`'s counter. One transaction commits all changes. Joined workspaces are counted only as memberships. The owner row serializes simultaneous creates/deletes across application processes; an in-memory lock is not involved. A bounded create lock wait returns 409 `IDEMPOTENCY_IN_PROGRESS` with `Retry-After: 1` for a safe same-key retry. A quota/business rejection or rollback consumes no key.

Workspace writers consistently lock **actor user → workspace → membership/invitation children → outbox**. Owner mutations check current visibility and role before preconditions. Member/invitation endpoints must lock the active workspace before changing child rows; parent-check triggers block active membership/invitation writes to a deleted workspace and serialize parent deletion against inserts. The [membership implementation](membership-permissions.md#transactions-generations-and-shared-primitives) now provides versioned joins/removals, the 20-member quota and shared helpers for invitation acceptance.

Cursor tokens contain 256 random bits; only their SHA-256 hashes are stored. Lookup authenticates the cursor and binds it to its actor, endpoint, ordering and last tuple. A forged, expired or wrong-user token receives 400 `INVALID_CURSOR`. Cursor lifetime is 25 hours and can span changing page limits; this is server-side cursor storage, with no signing secret to configure.

Successful-create records are retained for 25 hours (exceeding the contract's 24-hour minimum from commit under the bounded transaction timeouts). Deletion does not remove them. Run bounded expiry cleanup periodically; it removes only expired retry/cursor rows and never deletes tombstones, membership/invitation history or outbox events:

```powershell
# Running Docker application, or use uv run --locked python with host configuration.
docker compose --profile app exec -T identity python -m identity_workspace.workspaces.cleanup --limit 1000
```

Each invocation removes at most `--limit` records per table (1–10,000, default 1,000); repeat on a schedule if needed. Do not purge unexpired retry records.

## Authoritative deletion

One owner-authorized, version-checked transaction marks the workspace deleted, increments version, revokes every active membership, cancels pending invitations, releases the owned quota and inserts `workspace.deleted.v1` into the existing outbox. Accepted/declined invitation history remains unchanged. Any write failure rolls the entire deletion back. A simultaneous/repeated delete cannot release quota or emit the event twice.

The event uses a stable UUID, the deletion workspace version, propagated request UUID and a closed actor summary (`id`, `display_name`). It contains no email, Google subject, bearer token or invitation secret. The database protects event identity/payload/creation time from later updates, while permitting delivery bookkeeping (`attempts`, `next_attempt_at`, `published_at`). Shared JSON Schema validation runs in tests; PostgreSQL also bounds the stored payload size.

The endpoint returns 204 after this authoritative commit. It makes no synchronous call to Task or the broker. Task cleanup consumes the event asynchronously and must enforce its own terminal tombstone; delayed cleanup cannot restore Identity access. Pending outbox events survive broker downtime and must never be discarded as cursor/retry cleanup.

## Automated verification

Run the three suites and report guards from the README. Focused feature commands:

```powershell
& $identityUv run --locked pytest tests/unit/test_workspaces.py -q
& $identityUv run --locked pytest tests/request/test_workspaces.py -q
& $identityUv run --locked pytest tests/integration/test_workspaces.py -q
& $identityUv run --locked python scripts/smoke_container.py --image identity-workspace:workspace-check
```

Stop if any command returns a nonzero exit code. Integration and smoke tests create isolated disposable PostgreSQL projects with synthetic actors/sessions. No real Google configuration, broker, Task process or developer database is used. Invitation storage is exercised with real persisted fixtures for pending and terminal statuses. Tests cover all endpoints, real expired/revoked sessions, quota races, idempotent retry races, normalized replay/conflict, rollback, stale/no-op writes, cursor scope/expiry/ties, authorization ordering, atomic invitation/membership deletion, immutable events and migration upgrade/drift.

The image smoke script seeds an application-session fixture only in its disposable database, then exercises all five endpoints over real HTTP, create replay, ETags, missing/stale preconditions, quota release and a durable deletion event. It additionally checks packaged auth guards, database outage/recovery and credential-free logs. There is no fake-auth switch in the production service. CI discovers these tests in its existing unit/request/integration matrix and gates the lifecycle smoke test and image publishing on success. A local passing run is not a hosted GitHub Actions result; push/open the PR yourself and inspect the required checks.

## Integration handoff

The [invitation API](registered-user-invitations.md) now reuses this storage and lock order, with real acceptance and pending cancellation verified alongside workspace deletion. The lifecycle outbox publisher remains a separate child task and must publish the existing immutable records rather than introduce another outbox. Retain immutable membership generations and workspace tombstones. The original fixture checks below record the earlier workspace-task verification.

Before Task 3 is closed across the project, rerun deletion through the implemented invitation acceptance/cancellation paths and the lifecycle publisher/Task consumer: pending invites cancel, accepted members lose access, one immutable deletion event is delivered/retried, Task persists its terminal tombstone and cleanup stays asynchronous during outages. This branch does not claim broker delivery, private access endpoints or real Task cleanup have been implemented or verified. Keep the issue open for that cross-task integration and a passing hosted pipeline.

## Local verification recorded

Verified on 2026-11-16 with the locked dependencies:

- Windows: 305 unit tests, 214 request tests and 89 PostgreSQL integration tests passed (608 total); all three JUnit reports passed the nonempty/zero-failure/zero-skip guard.
- Isolated Linux Python 3.13.16/uv 0.13.8: 305 unit tests and 214 request tests passed with both report guards and dependency consistency checks. Integration tests were driven from Windows against real Linux PostgreSQL containers.
- The `identity-workspace:workspace-check` Linux/amd64 image built and passed the complete disposable smoke test, including authenticated workspace operations, replay, missing/stale preconditions, quota release, a pending deletion event, database outage/recovery and secret-free logs. Temporary containers/networks/volumes were removed afterwards. The ignored JSON report is `test-results/container-smoke.json`.
- The migration upgraded an existing authentication schema, preserved its users and passed Alembic schema-drift detection. Existing migrations and runtime dependency versions remain unchanged.
- actionlint 1.7.12 accepted the workflow; the existing test matrix and required aggregate continue gating image verification and main-only publishing.

These checks do not establish a hosted GitHub Actions result or verify invitation APIs, broker delivery or Task cleanup. No commit, push, PR, registry publication or deployment was performed.
