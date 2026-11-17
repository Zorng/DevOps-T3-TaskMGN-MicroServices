# Registered-user invitations and acceptance limits

This feature implements all six invitation operations in the [public Identity OpenAPI](../../../api_contracts/openapi/identity-workspace.openapi.json), following [authentication](../../../api_contracts/authentication.md), [shared conventions](../../../api_contracts/conventions.md), [acceptance workflows](../../../api_contracts/workflows.md) and [event envelopes](../../../api_contracts/events/README.md). It reuses the merged workspace storage, immutable membership-generation helpers and transactional outbox.

## Setup and migration

Python 3.13.16, PostgreSQL 18.7 and all locked dependencies remain unchanged. No additional installation, environment setting or secret is required. Google configuration is still needed for manual account sign-in; automated tests use synthetic sessions and provider doubles. Follow the [service README](../README.md#setup-and-local-verification). From the service directory, with Docker Desktop running:

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

Compose applies `0005_registered_invitations` before starting the service. For host Python, explicitly run `uv run --locked alembic upgrade head` before startup. Existing users, sessions, workspaces, memberships, invitation rows and immutable outbox events are preserved. The migration adds ordered invitation indexes, authenticated invitation cursor storage and a trigger protecting invitation identity and terminal outcomes. Successful-create ETags become nullable because invitations have no ETag; workspace ETags are unchanged. Downgrade is tested only on disposable storage; it preserves retry bodies/IDs and uses an unused empty ETag for invitation records when restoring the old non-null column.

## Lookup and creation

Only the current workspace owner can create an invitation. A current non-owner gets 403, and an inaccessible/deleted workspace gets 404 before email lookup. Lookup occurs only inside this POST, never through a public user-directory endpoint or an email URL/query parameter.

Trim surrounding whitespace, validate 1–254 Unicode code points and email syntax, then match the stored registered email case-insensitively. Do not remove dots or plus aliases. A user is still identified by Google subject; matching email never merges accounts.

| Condition | Result |
| --- | --- |
| No registered identity matches | 404 `USER_NOT_FOUND` |
| Multiple registered identities match | 409 `EMAIL_AMBIGUOUS` |
| Recipient already has an active membership, including the owner | 409 `ALREADY_MEMBER` |
| Same workspace/recipient already has a pending invitation | 409 `INVITATION_ALREADY_PENDING` |
| Valid invitation, even when workspace is full | 201, pending, with no member-slot reservation |

Creation requires a UUID `Idempotency-Key`. Scope is actor + POST + canonical workspace invitation path + key. The normalized email fingerprint, original 201 body, resource ID, invitation and `invitation.created.v1` event commit together. Retain successful records for 25 hours, exceeding the 24-hour minimum under bounded transactions. A replay reauthenticates and checks current owner/resource visibility before returning the saved historical response or a key conflict. Same normalized body/key returns the original response even after the invitation is answered or workspace renamed; a different body returns 409 `IDEMPOTENCY_CONFLICT`. No key is consumed on validation/business rejection or rollback. Concurrent creates serialize or receive 409 `IDEMPOTENCY_IN_PROGRESS` with `Retry-After: 1`.

Retry uncertain 500/503/network outcomes with the same key/body. An expired key can create another invitation after reconciliation; a still-pending invitation remains protected by the independent duplicate-pending check. Deleted/inaccessible workspaces return 404 instead of cached private data.

## Visibility, lists and pagination

Current owners list their workspace's invitations. Recipients list only their own invitations globally; they do not need to be members of the invited workspace. Detail is visible to the recipient or current workspace owner. Other actors get 404. A recipient's departure does not erase invitation history or prevent a same-terminal response replay.

Both lists support optional `status` (`pending`, `accepted`, `declined`, `cancelled`), `limit` (default 20, maximum 100) and `cursor`. Order is `created_at DESC, id DESC`. Each page is `{"items":[...],"next_cursor":null|"opaque-token"}`. Cursors contain 256 random bits; only hashes are stored, bound to actor, endpoint, workspace parent where applicable, status filter, ordering and last tuple. Tokens last 25 hours, and limit may change between pages. Scope/filter mismatch, forged/expired cursor: 400 `INVALID_CURSOR`. Other malformed/duplicate/unsupported parameters, including malformed path UUIDs, return 400. Pages recheck visibility/filter state and are not historical snapshots.

Invitation bodies contain ID, workspace ID/name, inviter/recipient ID/display-name summaries, status and timestamps. They never expose lookup email, Google subject or tokens. Names reflect current profiles/workspace state; create retries preserve their original saved body. There is no invitation ETag or If-Match requirement.

## Acceptance, decline and cancellation

| Current state | Recipient accepts | Recipient declines | Current owner cancels |
| --- | --- | --- | --- |
| pending | 200 accepted and one new membership, or 409 `MEMBER_LIMIT_REACHED` with invitation unchanged | 200 declined | 200 cancelled |
| accepted | 200 existing accepted record; no new membership | 409 `INVITATION_NOT_PENDING` | 409 `INVITATION_NOT_PENDING` |
| declined | 409 `INVITATION_NOT_PENDING` | 200 existing declined record | 409 `INVITATION_NOT_PENDING` |
| cancelled | 409 `INVITATION_NOT_PENDING` | 409 `INVITATION_NOT_PENDING` | 200 existing cancelled record |

Only the recipient responds; a visible current owner attempting response gets 403. Only the current owner cancels; a visible recipient attempting cancellation gets 403. Other actors get 404 before terminal-state conflicts. Missing, malformed, expired or revoked sessions get 401. Cancellation is bodyless; response JSON permits exactly `{"action":"accept"}` or `{"action":"decline"}`. Neither needs an idempotency key.

All mutations lock actor user → active workspace → invitation/member children. Acceptance uses `join_member` in the same transaction: check active workspace and pending invitation, count active members including owner, create a fresh immutable generation, increment workspace version and mark accepted. At capacity, the whole operation rolls back and leaves the invitation pending. Pending invitations never reserve space and have no expiry. Competing accept/cancel/decline calls serialize to one terminal outcome.

After leaving or owner removal, replaying an old accepted invitation returns its accepted record without restoring access. A fresh invitation and acceptance are required to rejoin with a new generation. Decline/cancel do not increment workspace version or emit another creation event. Repeated terminal actions preserve status/update time. A database trigger prevents changing invitation identity or reopening/retimestamping terminal rows.

Authoritative workspace deletion cancels pending invitations in its existing transaction, revokes membership and persists its deletion event. Accepted/declined/cancelled history is retained. All invitation detail/actions then return 404, owner listing returns 404 and recipient lists exclude that workspace, regardless of cleanup/notification delivery timing.

## Manual HTTP example

First register two users by Google sign-in; create a disposable workspace as the owner. Obtain each application access token from the exchange response in a private API-client variable. The browser verification helper intentionally keeps tokens in memory without displaying them. Use the recipient's registered email, not an unregistered Google account. Never put real lookup addresses or tokens in URLs, tracked files, command history or screenshots.

This PowerShell example prompts without echoing lookup/token values. It creates/retries an invitation, lists/details it, and accepts/repeats acceptance:

```powershell
$inviteApi = 'http://127.0.0.1:8000'
$inviteWorkspace = Read-Host 'Disposable workspace UUID'
$inviteOwnerSecret = Read-Host 'Owner application token (hidden)' -AsSecureString
$inviteRecipientSecret = Read-Host 'Recipient application token (hidden)' -AsSecureString
$inviteEmailSecret = Read-Host 'Registered recipient email (hidden)' -AsSecureString
$inviteOwnerHeaders = @{ Authorization = 'Bearer ' + ([pscredential]::new('session', $inviteOwnerSecret)).GetNetworkCredential().Password }
$inviteRecipientHeaders = @{ Authorization = 'Bearer ' + ([pscredential]::new('session', $inviteRecipientSecret)).GetNetworkCredential().Password }
$inviteEmail = ([pscredential]::new('email', $inviteEmailSecret)).GetNetworkCredential().Password
$inviteCreateHeaders = @{ Authorization = $inviteOwnerHeaders.Authorization; 'Idempotency-Key' = [guid]::NewGuid().ToString() }
$inviteBody = @{ email = $inviteEmail } | ConvertTo-Json -Compress
try {
    $inviteUrl = "$inviteApi/api/v1/workspaces/$inviteWorkspace/invitations"
    $invite = Invoke-RestMethod -Method Post -Uri $inviteUrl -Headers $inviteCreateHeaders -ContentType 'application/json' -Body $inviteBody
    Invoke-RestMethod -Method Post -Uri $inviteUrl -Headers $inviteCreateHeaders -ContentType 'application/json' -Body $inviteBody
    Invoke-RestMethod -Method Get -Uri "${inviteUrl}?status=pending&limit=20" -Headers $inviteOwnerHeaders
    Invoke-RestMethod -Method Get -Uri "$inviteApi/api/v1/invitations?status=pending" -Headers $inviteRecipientHeaders
    $inviteTarget = "$inviteApi/api/v1/invitations/$($invite.id)"
    Invoke-RestMethod -Method Get -Uri $inviteTarget -Headers $inviteRecipientHeaders
    Invoke-RestMethod -Method Post -Uri "$inviteTarget/response" -Headers $inviteRecipientHeaders -ContentType 'application/json' -Body '{"action":"accept"}'
    Invoke-RestMethod -Method Post -Uri "$inviteTarget/response" -Headers $inviteRecipientHeaders -ContentType 'application/json' -Body '{"action":"accept"}'
} finally {
    Remove-Variable inviteOwnerSecret, inviteRecipientSecret, inviteEmailSecret, inviteEmail, inviteOwnerHeaders, inviteRecipientHeaders, inviteCreateHeaders, inviteBody -ErrorAction SilentlyContinue
}
```

For a separate pending invitation, use action `decline`, or POST `/api/v1/invitations/{id}/cancel` with the owner token and no body. The transition table lists repeat/conflicting outcomes. A new intentional invitation needs a fresh create key; preserve the original key/body after an uncertain create result.

## Outbox integration and maintenance

The real creation event uses aggregate ID = invitation ID, aggregate version = 1, stable event UUID, request UUID, workspace/invitation/recipient IDs and the inviter's ID/display-name snapshot. It contains no recipient email or copied workspace content. It commits with invitation/retry storage and remains durable during broker outage. Contract review also corrected new removal events to use their membership ID and terminal generation version 2; workspace version increments remain separate. Tests assert these semantic envelope rules in addition to JSON Schema shape.

The lifecycle publisher task must deliver the saved event ID/payload unchanged with durable retry/confirmation; Notification must deduplicate and recheck invitation access. No broker publisher or Notification consumer is claimed here. The new smoke check proves real invitation-to-membership-to-deletion integration on the Identity side; final broker/consumer integration remains a separate completion gate.

Expired page tokens can be removed in bounded batches. Successful-create record cleanup is shared across workspace and invitation scopes:

```powershell
docker compose --profile app exec -T identity python -m identity_workspace.invitations.cleanup --limit 1000
docker compose --profile app exec -T identity python -m identity_workspace.workspaces.cleanup --limit 1000
```

Each limit is 1–10,000. Neither command expires/deletes invitations, membership history or pending outbox events. Cursor expiry is independent of the invitation's lifetime.

## Automated verification

Run the three suites/report guards from the README. Focused feature checks:

```powershell
& $identityUv run --locked pytest tests/unit/test_invitations.py -q
if ($LASTEXITCODE -ne 0) { throw "Invitation unit tests failed." }
& $identityUv run --locked pytest tests/request/test_invitations.py -q
if ($LASTEXITCODE -ne 0) { throw "Invitation request tests failed." }
& $identityUv run --locked pytest tests/integration/test_invitations.py -q
if ($LASTEXITCODE -ne 0) { throw "Invitation database tests failed." }
& $identityUv run --locked python scripts/smoke_container.py --image identity-workspace:invitation-check
if ($LASTEXITCODE -ne 0) { throw "Invitation container smoke failed." }
```

Tests use synthetic registered identities/sessions and isolated PostgreSQL 18.7, never a developer database or real Google/broker credentials. They cover all six endpoints, owner/recipient boundaries, normalized/ambiguous lookup, aliases, duplicate creation and idempotency, terminal matrices/retries, accepted-after-leave replay, capacity and state races, rollback, authenticated pagination/filter/order, deletion visibility and migration preservation/drift. Request logs exclude lookup values and tokens.

Docker smoke seeds registered users/sessions only, then creates/accepts invitations and joins/rejoins through the public endpoints. It verifies lists/detail, repeats/conflicts, membership generations, pending cancellation and invisibility on deletion, and all three Identity event envelope conventions. Existing auth, migration, outage/recovery and secret-safe logging checks remain. The CI matrix and required aggregate gate image verification/publishing; a hosted result requires the user to push/open the PR.

## Local verification recorded

Verified on 2026-11-17 with unchanged locked dependencies:

- Windows: 412 unit tests, 366 request tests and 200 real PostgreSQL integration tests passed (978 total), with zero failures/errors/skips and all three nonempty report guards passing.
- Isolated Linux Python 3.13.16/uv 0.13.8: the same 412 unit and 366 request tests passed, including final malformed-path handling, with report guards and dependency checks. PostgreSQL integration was driven from Windows against disposable Linux containers.
- `identity-workspace:invitation-check` built and passed the full Linux/amd64 container smoke test: all invitation/member/workspace operations, replay and terminal semantics, real joins/rejoins, pending cancellation on deletion, exact aggregate conventions, migrations, outage/recovery and secret-safe logs. The ignored report is `test-results/container-smoke.json`.
- Shared contract validation passed (4 OpenAPI documents, 41 operations, 43 fixtures, 51 examples, 5 event variants, 14 negative cases). Migration preservation/round-trip and Alembic schema-drift checks passed.
- actionlint 1.7.12, dependency consistency and local Markdown links passed. Git candidate files contained no matches for ignored local database passwords; environment/secrets/tools/reports remained ignored.

Temporary test containers/networks/volumes were removed; regular local containers and databases were not upgraded or restarted. Nothing was committed, pushed, published or deployed. Hosted CI and final broker/Notification/Task consumer delivery remain unverified until their separate runs/integration.
