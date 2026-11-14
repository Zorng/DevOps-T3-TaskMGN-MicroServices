# Cross-Service Workflows

## Service authority and boundaries

Identity & Workspace owns identities, sessions, workspaces, memberships, invitations, and workspace/member quotas. Task owns boards, tasks, comments, the board quota, task numbering, and task-related outboxes/cleanup. Notification owns notification records and read state. Gateway & Infrastructure owns gateway/broker deployment and network configuration. Frontend uses public gateway APIs; it does not access databases, private APIs, or the broker.

Every service owns and migrates its own database. There are no cross-database joins or distributed database transactions. A UI can combine workspace/member data from Identity & Workspace with board/task data from Task.

## Sign-in and first workspace

1. Complete the challenge and Google exchange described in authentication.md.
2. POST workspace with an Idempotency-Key. Identity & Workspace locks the user's ownership quota, checks fewer than 10, creates workspace/owner membership, and commits together.
3. New workspace has no boards. Frontend creates desired boards using Task's API. Task obtains current access from Identity & Workspace, locks its local workspace board quota, creates a board, and commits. Identity & Workspace does not store board counts or board data.
4. Retry lost create responses with the same keys; never create another workspace/board accidentally.

## Invitation and joining

1. Owner posts the recipient's exact email. Identity & Workspace verifies owner, resolves registered user, rejects existing membership/pending invitation, and atomically writes invitation + outbox.
2. Notification consumes invitation.created.v1 and creates recipient notification. Broker downtime does not undo the invitation.
3. Recipient can list their invitations directly even if notification delivery is delayed.
4. Accept locks the invitation and workspace membership quota, verifies active workspace and pending state, creates a new immutable membership ID, increments workspace version/member count, and marks accepted in one transaction.
5. At capacity, return 409 and leave pending. A same-action retry on accepted/declined returns the terminal record without another side effect. Conflicting terminal action returns 409. An accepted invitation cannot rejoin someone who subsequently left.
6. Owner cancellation and recipient response serialize against the same invitation. Exactly one terminal state wins. Workspace deletion overrides visibility; its invitations become inaccessible through public APIs.

## Creating/updating/assigning/moving tasks

1. Task calls Identity & Workspace's private access endpoint with its service key and original actor bearer. Require live membership. Validate destination board belongs to the same workspace, and assignee is a current member.
2. Store assignee's membership ID and the access snapshot's workspace version internally. A null assignment stores neither.
3. Create/update the task under local transaction and If-Match for update. A board move changes only board relation/update metadata; the task ID/number/comments stay stable. Destination must be active at commit.
4. When the new assignee is another user and differs from the effective prior assignee, append task.assigned.v1 in that transaction. A no-op update does not increment version or append an event.
5. Notification consumes and deduplicates. Notifications reference the task, not its old board, so links survive moves.

For a PATCH combining fields, validate the resulting whole task before committing anything. On invalid date pair, non-member target, stale version, or unavailable Identity, no requested fields or events are committed.

## Comments and mentions

Frontend gets current members from Identity & Workspace, uses an explicit picker, and sends plain body plus user-ID/range pairs. Task obtains a fresh access snapshot, validates exact code-point ranges and all members, saves comment with author/mention name snapshots, and appends one event per distinct non-author recipient atomically. Repeated mention ranges do not duplicate recipient notifications. Comments are immutable; there are no update/delete routes.

## Member removal, rejoin, and races

Identity & Workspace deletes the current membership generation (retaining lifecycle history), increments workspace version, and writes workspace.member_removed.v1 in one transaction. New requests checked afterward get 404; queued events are not an authorization mechanism.

Task's consumer writes a removal tombstone and clears assignments matching that generation only, incrementing affected task versions. Rejoin creates a new membership ID, so an old removal event cannot clear new-generation assignments.

Because access-check and task-commit span services, v1 defines authorization at the fresh access-check snapshot. A request checked before removal may finish. Avoid claiming instantaneous cancellation of in-flight requests. Task must handle these orderings:

- **Cleanup before old request commit:** local mutation and cleanup lock/check the same membership/workspace tombstone records. A mutation whose actor, requested assignee, or mentioned member generation is tombstoned rejects with 404 (actor) or 422 (assignee/mention), rather than resurrecting removed state.
- **Old request commit before cleanup:** cleanup clears the matching old assignment after delivery.
- **Cleanup delayed:** task reads compare stored assignments with a fresh member snapshot and clear old generations transactionally before returning data. New permission checks still deny the removed actor.
- **Stale snapshot versus a newer assignment:** record the snapshot workspace version used for each assignment. Reconcile only if the read snapshot version is at least the assignment's recorded version. If older, refresh access; do not clear the new assignment. Conditional local updates compare task version/generation to avoid races. Persistent inability to obtain a fresh snapshot returns 503.

Use a consistent local lock order and compare-and-update conditions for mutation, read reconciliation, and consumers. Retry conflicts internally within bounded request time. Reconciliation clearing an assignment emits no notification. Retain creation-time author snapshots on comments.

## Workspace and board deletion

Identity & Workspace workspace deletion atomically marks the workspace inactive, revokes memberships, cancels pending invites, releases owner's quota, increments version, and emits workspace.deleted.v1. Returns 204 after this commit. A task API call checks Identity & Workspace and denies access even before cleanup arrives.

Task consumes workspace deletion, persists a terminal tombstone, and makes all workspace boards/tasks/comments inaccessible. Physical deletion may run in batches. New IDs never reuse the deleted workspace. Local tombstone locking prevents an old in-flight request from inserting after cleanup; inserts committed earlier are included in cleanup.

Board deletion is local to Task: require current owner permission and If-Match, atomically hide the board and descendants and release the board slot. Task deletion is similarly local. Reads must always enforce active parent visibility, including during deferred physical cleanup. Existing notifications remain generic records; following a deleted target returns 404.

## Notification reads

Notification introspects the caller's opaque session through Identity & Workspace, scopes every query to that user, and returns generic historical metadata. Frontend chooses wording from type (invitation/assignment/mention), then fetches the linked invitation/task through its owning API if opened. No direct event payload is trusted as current entity state.

Mark-read is atomic and idempotent: first transition sets read_at; later calls return it unchanged. List/count calls are separate snapshots, so their counts may differ briefly under concurrent delivery/read actions. Frontend refreshes after updates. No background polling interval is mandatory.

## Dependency failures

| Failure | Required behavior |
| --- | --- |
| Identity unavailable on a protected Task/Notification request | 503; no stale-identity fallback and no mutation |
| Producer database commit fails | No business change/event; 500 or 503 |
| Broker unavailable after local commit | Successful API response; pending outbox retained/retried |
| Consumer crashes before commit | Redeliver; no partial notification/effect |
| Consumer crashes after commit before acknowledgement | Redeliver; dedup marker prevents duplicate effect |
| Poison/unsupported event | Durable dead-letter record with diagnostic reason; alert and operator replay |
| Response lost after successful create | Same Idempotency-Key replays success after access check |
| Updated entity since client's read | 412; Frontend reloads and reconciles |

## Design review tradeoffs

This design favors understandable correctness over maximal availability: every protected request depends on Identity, and the frontend signs in again after a reload. There is no synchronous distributed transaction for removal/cleanup. If the team wants persistent browser sessions, cached permissions, or stronger in-flight revocation guarantees, revise the authentication/workflow contracts before implementing those behaviors independently.
