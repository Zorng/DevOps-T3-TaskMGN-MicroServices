# Shared REST Conventions

These rules are normative for contract v1.0.0 and supplement the machine-readable schemas.

## Transport and identifiers

- Public base path `/api/v1`; deployment uses HTTPS. Local development gateway: `http://localhost:8080`.
- Requests and responses use UTF-8 JSON, `Content-Type: application/json`. Reject a body on a bodyless operation with 400; do not require Content-Type when no body is present. Unsupported media type with a body: 415.
- Entity, request, event, and membership IDs are UUID strings. Use server-generated UUIDv4 for entities; clients generate UUIDv4 for idempotency keys. IDs are immutable and never reused.
- Timestamps are RFC 3339 UTC strings ending in `Z`; use millisecond precision consistently. Examples without fractional seconds are also valid. Dates are ISO `YYYY-MM-DD`, not timezone-adjusted instants.
- JSON keys are snake_case. Unknown JSON request properties are 422; unsupported query parameters are 400 MALFORMED_REQUEST. Requiredness, nullability, and bounds are defined in schemas; do not coerce strings into numbers/booleans.
- Private credentials, Google tokens, app tokens, and email lookup values must not appear in logs or URLs.
- Responses return `Cache-Control: no-store` and `X-Request-ID`. Public gateway generates a UUID if the incoming request ID is missing/invalid. Forward it through private calls and originating events.
- Maximum JSON body: 64 KiB measured as UTF-8 bytes; larger requests receive 413. The 16,384-character Google token limit is an additional field bound, not a user-content limit.

## Input and content validation

Count text lengths and mention offsets in Unicode code points, not UTF-16 units or bytes. Trim surrounding whitespace from workspace/board names, titles, and invitation emails before checking lengths. Reject whitespace-only names, titles, and comment bodies. Do not trim comment bodies: that would change mention offsets. Plain-text descriptions/comments are escaped when rendered.

Workspace names: 1–100; board names: 1–50; titles: 1–200; descriptions: 0–5,000; comments: 1–2,000; invitation emails: 1–254; optional member-search query: 0–100. Duplicate workspace and board names are permitted; IDs distinguish them. Dates must be real calendar dates. Past dates are allowed. If both dates exist, `start_date <= due_date`, evaluated on the resulting task after a PATCH.

Optional create fields default to null (dates/assignee), empty description, and empty mentions. PATCH omits unchanged fields, accepts null only for nullable fields, and rejects an empty object. Description is cleared with `""`, not null. A task has no independent status/priority/custom-field property; `board_id` determines status.

Google-derived display names are normalized to non-empty plain text, capped at 200 Unicode code points, with `User` as fallback. This normalization applies to provider data, not silently truncating user-authored text. UserSummary snapshots on tasks/comments/events remain stable historical labels; `GET /me` and the current member list provide current names.

Mention range `start` is zero-based and `length` is positive. The range must fit completely in the exact body, start with `@`, and contain at least one following character. Ranges cannot overlap. The UI's explicit picker supplies `user_id`; literal unselected @text produces no mention. Repeated ranges for the same user are permitted but generate one event per distinct non-author user. Cap at 50 ranges and 20 distinct current members. Invalid ranges or a user no longer present in the access snapshot: 422.

## Pagination and filtering

Paginated lists return `{ "items": [...], "next_cursor": null | "opaque-token" }`. Default limit 20; maximum 100. Members (at most 20) and boards (at most five) are unpaginated. Search is only the optional member `q` query; task filters include only `board_id`, invitations only `status`, notifications only `unread_only`.

Use keyset pagination: most lists sort `created_at DESC, id DESC`; comments sort ascending. Member and board order is documented in OpenAPI. Cursor binds ordering, filters, actor, parent resource, and last sort tuple; sign or authenticate it. Clients must not construct it. A cursor used with different scope/filters returns 400 INVALID_CURSOR. The limit may change. New inserts/updates mean pages are not a historical snapshot; unread lists may shrink as items are read. Last page always has null cursor.

## Errors

```json
{"error":{"code":"VALIDATION_FAILED","message":"The request contains invalid fields.","request_id":"00000000-0000-4000-8000-000000000012","details":[{"field":"due_date","reason":"Must be on or after start_date."}]}}
```

`details` is always an array, possibly empty. Clients branch on `code`, not English messages. Never include stack traces, tokens, raw provider errors, or hidden-resource details.

| HTTP | Codes and meaning |
| --- | --- |
| 400 | MALFORMED_REQUEST, INVALID_CURSOR, IDEMPOTENCY_KEY_REQUIRED: malformed syntax/parameters or missing required create key |
| 401 | AUTH_REQUIRED, SESSION_INVALID, GOOGLE_TOKEN_INVALID, AUTH_CHALLENGE_INVALID, SERVICE_CREDENTIAL_INVALID |
| 403 | FORBIDDEN, ORIGIN_NOT_ALLOWED: known accessible resource but action forbidden, or browser origin denied |
| 404 | NOT_FOUND: missing/deleted/outside actor's workspace; USER_NOT_FOUND only for owner-only exact-email invitation |
| 409 | WORKSPACE_LIMIT_REACHED, BOARD_LIMIT_REACHED, MEMBER_LIMIT_REACHED, ALREADY_MEMBER, INVITATION_ALREADY_PENDING, INVITATION_NOT_PENDING, OWNER_CANNOT_LEAVE, IDEMPOTENCY_CONFLICT, IDEMPOTENCY_IN_PROGRESS, EMAIL_AMBIGUOUS |
| 412 | VERSION_CONFLICT: resource changed since supplied If-Match |
| 413 | PAYLOAD_TOO_LARGE |
| 415 | UNSUPPORTED_MEDIA_TYPE |
| 422 | VALIDATION_FAILED: field bounds, dates, ranges, wrong-workspace destination, or non-member assignee/mention |
| 428 | PRECONDITION_REQUIRED: missing If-Match |
| 429 | RATE_LIMITED: include Retry-After seconds |
| 500 | INTERNAL_ERROR |
| 503 | DEPENDENCY_UNAVAILABLE: include Retry-After seconds; never authorize from stale cache |

Use 404 for another user's notification or inaccessible workspace content. Use 403 for a current member attempting an owner-only operation. A destination board that cannot be used is a generic 422 `board_id` validation error; do not reveal its actual owner/workspace. Validate caller access before returning state/version conflicts.

## Concurrent updates

Workspace, board, and task records have positive integer `version`. Entity GET/create/PATCH returns `ETag: "<version>"`; list items also include version. PATCH and DELETE of those records require exact `If-Match`, no wildcard. Validate and mutate atomically; stale version is 412, missing is 428. No automatic merging. Frontend reloads and asks the user to reconcile changes.

Increment the relevant resource version on actual persisted changes. Identical field values are a no-op, preserve version, and emit no event. Workspace member joins/removals increment workspace version because member_count changes. Task assignment cleanup increments task version. Board/task deletion checks its current version before hiding it. Workspace deletion increments its version for the deletion event. Task comment creation does not change task version.

Task reads reconcile assignments whose stored membership generation is absent from the authoritative snapshot: clear them transactionally and increment task version before returning data/ETag. Do not clear a concurrently changed assignment: compare stored generation in the update. Snapshot comparison rules and removal races are in workflows.md. This avoids returning a changed representation under an unchanged ETag. Private generation/tombstone fields are never public task fields.

Quota checks and increments must be transactional: concurrent calls cannot exceed 10 owned workspaces, five boards, or 20 members. Task number allocation is also transactional and monotonic within workspace; numbers are never recycled (gaps are permitted).

## Idempotent creates and retry rules

Workspace, board, task, comment, and invitation POST creates require `Idempotency-Key`. Persist key, normalized request fingerprint, successful status/body/ETag, and original resource ID in the same local transaction as the mutation/outbox. Scope is authenticated user + method + canonical path + key. Retain at least 24 hours from commit. A retry after expiry may create another resource; clients should reconcile before retrying old actions.

Same scope/key/body returns the original success without creating another entity/event. Different body is 409 IDEMPOTENCY_CONFLICT. Concurrent identical requests serialize or return 409 IDEMPOTENCY_IN_PROGRESS; retry after a short delay. Do not consume the key on a validation/business rejection or rollback. Reauthenticate and check current permission/resource visibility before replay; a deleted or inaccessible original resource returns 404 instead of a cached private body. Original response may be historical if the entity was edited later; GET it for current state.

Invitation accept/decline/cancel and mark-read are state-based idempotent operations and do not need a key. Repeating accept after subsequent departure returns the old accepted invitation and does not rejoin the user. A new invitation is required. Logout may return 401 when retried with its now-revoked session; Frontend still clears local state. PATCH retries after an unknown result should GET to reconcile the version rather than automatically apply again.

A local commit can succeed even if a connection breaks. For ambiguous 500/503/network outcomes, retry creates with the same key, never a fresh one. Gateway must not automatically retry mutations. Task-to-Identity read-only POST checks use a 2-second timeout and at most one retry for transport errors/503; never retry 401/403/404. Gateway upstream timeout: 10 seconds, mapped to 503. Values are initial contract defaults; configure consistently if changed by team review.
