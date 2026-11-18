# Workspace membership and owner permissions

This feature implements member listing/search, owner removal and non-owner leave from the [public Identity contract](../../../api_contracts/openapi/identity-workspace.openapi.json). It uses existing application sessions, workspace/owner storage and the lifecycle outbox, following [shared conventions](../../../api_contracts/conventions.md), [authentication](../../../api_contracts/authentication.md), [workflow rules](../../../api_contracts/workflows.md) and the [removal event schema](../../../api_contracts/events/event.schema.json).

## Setup and upgrade

The supported Python 3.13.16, FastAPI, PostgreSQL 18.7 and locked dependencies are unchanged. No new package, environment variable, credential or runtime service is required. Follow the [service README](../README.md#setup-and-local-verification) to preserve/generate ignored local configuration and start Docker:

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

Compose upgrades through `0004_membership_generations` and the follow-up invitation migration `0005_registered_invitations` before starting this application. Host Python users run `uv run --locked alembic upgrade head` before startup. The membership migration preserves existing users, sessions, workspaces and membership rows, and adds a database trigger: generation ID/workspace/user/role/join time cannot change, and revocation cannot be undone or retimestamped. No old migration is rewritten. Downgrading removes this guard; it is tested only on disposable databases.

## Endpoint and permission rules

| Operation | Current owner | Current member | Outsider, departed member or deleted workspace |
| --- | --- | --- | --- |
| GET `/api/v1/workspaces/{workspace_id}/members` | 200 member list | 200 member list | 404 |
| DELETE `/api/v1/workspaces/{workspace_id}/members/{user_id}` | Remove current non-owner: 204; absent target: 204; owner target: 409 `OWNER_CANNOT_LEAVE` | 403, including removal of self/owner/unknown user | 404 |
| DELETE `/api/v1/workspaces/{workspace_id}/membership` | 409 `OWNER_CANNOT_LEAVE` | Leave: 204; subsequent attempt: 404 | 404 |

All require an unexpired, non-revoked application bearer session. Missing/malformed/expired/revoked sessions receive 401. A departure does not log the user out globally. New workspace reads, member reads, mutations and access snapshots deny the departed user immediately after the local revocation commits. Requests that already obtained an access snapshot may complete according to the cross-service contract; events do not authorize access.

These DELETE endpoints are bodyless and do not require `If-Match` or `Idempotency-Key`. They revoke the current generation under a lock. A repeated owner removal of an absent target makes no version/event change; repeated leave cannot pass the current-membership check. A removal targeting a user who subsequently rejoined operates on their current generation; downstream cleanup for an earlier event must use its earlier membership ID.

Real joins/removals increment workspace version and update its timestamp. Consequently, a workspace ETag fetched before a join/removal becomes stale for rename/delete. Workspace ownership quota is unchanged by joining, removing or leaving. The owner remains a member until authoritative workspace deletion.

## Listing and search

The response is `{"items":[...]}`, with at most 20 active memberships, ordered by join timestamp then immutable membership UUID ascending. Each item contains exactly `membership_id`, `user` (`id`, `display_name`), `role` and `joined_at`. Email, Google subject, session data and revoked history are not returned. The list is unpaginated; `limit`/`cursor`/`email` parameters are unsupported.

Optional `q` matches a substring of display name case-insensitively, preserving the same ordering. Its bound is 0–100 Unicode code points; absent/empty searches list all active members. Search text is not trimmed. SQL parameters are bound, and `%`, `_` and the escape character are literal text rather than wildcard expansion, using [SQLAlchemy's escaped case-insensitive containment](https://docs.sqlalchemy.org/en/21/core/sqlelement.html#sqlalchemy.sql.expression.ColumnElement.icontains). Email addresses are never searched. Malformed, duplicate or oversized query parameters return 400; unsupported query parameters and request bodies also return 400. Search input is omitted from structured logs and metric labels.

## HTTP examples

These commands assume a disposable workspace already contains two registered users, one owner and one non-owner. First create and accept an invitation using the [registered-user invitation guide](registered-user-invitations.md). Integration tests still use isolated fixtures; Docker smoke now joins/rejoins through the real invitation endpoints. No unauthenticated join or test-auth bypass is introduced.

Use application tokens returned by Google exchange, not Google ID tokens. Prompt without echoing a real token and keep it only in shell memory. Do not commit tokens, put them in URLs/command history or copy them into logs/screenshots. From PowerShell:

```powershell
$membershipApi = 'http://127.0.0.1:8000'
$membershipWorkspaceId = Read-Host 'Existing disposable workspace UUID'
$membershipTargetUserId = Read-Host 'Non-owner user UUID to remove'
$membershipSecure = Read-Host 'Owner application access_token (hidden)' -AsSecureString
$membershipHeaders = @{ Authorization = 'Bearer ' + ([pscredential]::new('session', $membershipSecure)).GetNetworkCredential().Password }
try {
    # 200: current members only, with no email addresses.
    Invoke-RestMethod -Method Get -Uri "$membershipApi/api/v1/workspaces/$membershipWorkspaceId/members" -Headers $membershipHeaders
    # 200: literal case-insensitive display-name search.
    $membershipSearch = [uri]::EscapeDataString('Alice')
    Invoke-RestMethod -Method Get -Uri "$membershipApi/api/v1/workspaces/$membershipWorkspaceId/members?q=$membershipSearch" -Headers $membershipHeaders
    # 204 twice: removal commits once; absent target produces no second event.
    $membershipRemoveUrl = "$membershipApi/api/v1/workspaces/$membershipWorkspaceId/members/$membershipTargetUserId"
    (Invoke-WebRequest -UseBasicParsing -Method Delete -Uri $membershipRemoveUrl -Headers $membershipHeaders).StatusCode
    (Invoke-WebRequest -UseBasicParsing -Method Delete -Uri $membershipRemoveUrl -Headers $membershipHeaders).StatusCode
} finally {
    Remove-Variable membershipHeaders, membershipSecure -ErrorAction SilentlyContinue
}
```

To exercise leave, use a current non-owner's application token and DELETE `/api/v1/workspaces/{workspace_id}/membership`; expect 204, then 404 on repeat. With an owner's token expect 409. A current non-owner calling the owner-removal URL receives 403. A removed user's still-live session can call `/api/v1/me` and access other joined workspaces, but member/workspace requests for this workspace receive 404.

## Transactions, generations and shared primitives

Mutators lock actor user → active workspace → membership/invitation children → outbox. Membership reads/access snapshots hold a workspace SHARE lock; membership/workspace writers hold its UPDATE lock. Session and actor membership are rechecked after waiting for the workspace lock. Revocation, workspace version/time and the outbox insert commit or roll back together. There is no synchronous Google, Task or broker dependency.

`memberships.primitives` exposes transaction-local helpers for other Identity features:

| Primitive | Contract |
| --- | --- |
| `initialize_owner(session, workspace_id, owner_id, now)` | Used by workspace creation; inserts a fresh initial owner generation and preserves version one |
| `join_member(session, workspace_id, user_id)` | After the caller authorizes invitation acceptance, lock the active workspace, reject an existing member or a full 20-member quota, insert a fresh UUID generation and increment workspace version |
| `current_membership` / `require_membership` | Look up the current non-revoked generation; require returns 404 when absent |
| `locked_workspace` | Require an active parent and acquire a shared read or exclusive write lock |
| `active_members` | Build ordered, closed member summaries; caller must already authorize workspace access and hold its lock |
| `access_snapshot(session, workspace_id, actor_id)` | Fresh projection matching the private `AccessContext` schema: workspace version, actor generation/role and current members under the shared lock |

The caller supplies one `Database.transaction()` session and must let failures roll back the whole operation. Helpers never open or commit a separate invitation transaction. Before calling `join_member`, invitation acceptance must authenticate the recipient, lock workspace before invitation, verify its pending/terminal state and write acceptance in that same transaction. Repeating an already accepted invitation must return its terminal record without calling `join_member`; an old acceptance cannot rejoin someone who left. The caller must never treat these internal helpers as an authenticated public join API.

Membership rows are retained after revocation, including their IDs and original join times. Rejoin inserts another row with a fresh UUID; it never restores an old row. Database triggers prohibit identity rewrites and reversed revocations. Active membership uniqueness and owner checks remain in the previous migration. Concurrency tests verify only one new generation can fill the twentieth slot. As with workspace mutations, the bounded maximum workspace version cannot overflow; an exhausted version causes a safe 503 and rollback.

`MembershipStore.access_snapshot` additionally authenticates a hashed live session. The transaction-only snapshot primitive expects the caller to perform session/service authentication. The [private-access API](private-access-apis.md) now wraps these helpers with the Task service-key allowlist and the original bearer; Notification is limited to introspection.

## Removal event and integration handoff

Removal/leave appends the real `workspace.member_removed.v1` event into the existing outbox in the same transaction as revocation. Its aggregate ID is the membership ID and its aggregate version is the terminal generation version `2`, as specified in the shared event contract; the workspace's version increments separately. It contains the propagated request UUID and `data.workspace_id`, `data.user_id`, exact revoked `data.membership_id`, actor ID/display-name snapshot, and reason `removed` or `left`. Event identity/payload are immutable under the existing outbox guard. It contains no email, Google subject or credentials. Concurrent leave/removal emits exactly one event for the winning revocation; a replayed absent-target removal emits none.

The lifecycle outbox task must publish the saved payload/ID unchanged, retry durably during outages and preserve pending events. Task cleanup must persist a tombstone for the matching workspace/user/**membership generation**, clear assignments of that generation and preserve any later rejoined generation. Final removal-event integration depends on that publisher/consumer work; saving an event locally does not prove broker delivery or Task cleanup. Keep that integration item open until delivery/retry/out-of-order behavior is demonstrated alongside the lifecycle task.

## Automated verification

Run all three suites and CI report guards from the README. Focused checks:

```powershell
& $identityUv run --locked pytest tests/unit/test_memberships.py -q
if ($LASTEXITCODE -ne 0) { throw "Membership unit tests failed." }
& $identityUv run --locked pytest tests/request/test_memberships.py -q
if ($LASTEXITCODE -ne 0) { throw "Membership request tests failed." }
& $identityUv run --locked pytest tests/integration/test_memberships.py -q
if ($LASTEXITCODE -ne 0) { throw "Membership database tests failed." }
& $identityUv run --locked python scripts/smoke_container.py --image identity-workspace:membership-check
if ($LASTEXITCODE -ne 0) { throw "Container smoke test failed." }
```

Integration tests use their own PostgreSQL 18.7 project and synthetic users/sessions. They cover current-member authorization, literal search/no email lookup, order ties, capacity races, owner boundaries, repeat semantics, immediately denied access, exact event generation/reason, rollback, invitation/join transaction sharing, rejoin, immutable history, private snapshot shape and migration preservation/drift. Google verification remains doubled/blocked; no real Google credentials or developer database are used.

The container smoke test seeds only its disposable database, then calls all three member endpoints over real HTTP. It removes and rejoins the same user, verifies distinct generations and reason-specific persisted events, then leaves and checks repeat/access behavior. It retains the existing workspace/auth/health, migration, database outage/recovery and safe logging checks. All suite reports must be nonempty with zero failures/errors/skips. The existing CI matrix gates this smoke test, required aggregate and main-only image publishing. Hosted GitHub checks still need the user to push/open the PR; no local result establishes a passing hosted pipeline.

## Local verification recorded

Verified on 2026-11-17 with the unchanged locked dependencies:

- Windows: 350 unit tests, 260 request tests and 135 PostgreSQL integration tests passed (745 total). All three reports passed the nonempty/zero-failure/zero-error/zero-skip guard.
- Isolated Linux Python 3.13.16/uv 0.13.8: the same 350 unit and 260 request tests passed, with report guards and dependency consistency checks. Real PostgreSQL integration was driven from Windows against disposable Linux containers.
- The Linux/amd64 image `identity-workspace:membership-check` built and passed the complete container smoke test, including real member HTTP operations, owner boundaries, literal search, remove/rejoin/leave, distinct generations and durable reason-specific events without a broker. Database outage/recovery and secret-safe logging passed; the ignored report is `test-results/container-smoke.json`.
- Migration upgrade/downgrade preserved existing membership IDs and passed Alembic drift detection. actionlint 1.7.12 accepted the workflow; dependency consistency and local Markdown link checks passed.
- Git candidate files contained no matches for ignored local database passwords. Environment files, secrets, temporary tools and reports remained ignored.

All disposable test containers/networks/volumes were removed. Regular local application/database containers were not upgraded or restarted. No commit, push, PR, registry publication or deployment was performed. Hosted CI and lifecycle publisher/Task consumer integration remain separate verification steps.

Subsequent confirmed producer delivery is documented in the [lifecycle outbox report](../../../docs/identity-lifecycle-outbox-report.md). Its real-broker checks supersede the earlier producer-only delivery status; actual Task/Notification consumer effects remain a separate demonstration gate.
