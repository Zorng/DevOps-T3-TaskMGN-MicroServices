# Event Contract and Delivery Rules

Version: 1.0.0. The [schema](event.schema.json) is a discriminated union of five event variants. [Examples](../examples/events.json) validate against it. This is a broker-neutral contract, not an AsyncAPI file or a selected broker configuration.

## Routing and ownership

| Event type / logical routing key | Producer | Logical durable consumer queue | Purpose |
| --- | --- | --- | --- |
| invitation.created.v1 | Identity & Workspace | notifications.v1 | Invitation notification |
| task.assigned.v1 | Task | notifications.v1 | New assignee notification |
| task.comment_mentioned.v1 | Task | notifications.v1 | One mentioned recipient notification |
| workspace.member_removed.v1 | Identity & Workspace | task-cleanup.v1 | Clear assignments for one membership generation |
| workspace.deleted.v1 | Identity & Workspace | task-cleanup.v1 | Hide/remove workspace task data |

Gateway & Infrastructure maps these logical routes to the chosen broker, with persistent messages, durable subscriptions/queues, acknowledgements, publisher confirmations, and a separate dead-letter queue for each consumer queue. Use competing workers within each logical queue; do not create a new queue per replica. Provision queues/bindings before enabling producers. Ensure unroutable publications are detected and not marked published. Producer identities can publish only their allowed types; consumer identities can consume only their assigned queues. The API gateway is not on this path.

## Envelope

Every event has a stable UUID `id`, exact `type`, `schema_version: 1`, producer, UTC occurrence time, originating `request_id`, aggregate ID/version, and type-specific data. Retries reuse the identical ID and payload. New events get new IDs. Maximum encoded message size is 64 KiB.

| Type | aggregate_id | aggregate_version |
| --- | --- | --- |
| invitation.created.v1 | Invitation ID | 1 |
| task.assigned.v1 | Task ID | Task version committed with assignment |
| task.comment_mentioned.v1 | Comment ID | 1; separate event ID per distinct non-author recipient |
| workspace.member_removed.v1 | Membership ID | 2, terminal transition from active generation version 1 |
| workspace.deleted.v1 | Workspace ID | Version after deletion transition |

There are no contiguous-sequence or global-order guarantees: not every task update emits an event. The member removal event covers both owner removal (`removed`) and voluntary departure (`left`). Workspace deletion is one workspace event, not a required per-member event fanout. A workspace UUID is never reused.

Notification events contain only IDs and the actor's bounded display-name snapshot. They do not copy task titles, descriptions, comment bodies, Google identities, session tokens, or recipient emails. A recipient's membership ID records which membership existed at the action. Invitation recipients are not yet members, so that event has no recipient membership ID.

## Publisher outbox

Each producer writes domain changes and immutable outbox records in one database transaction. Emit events for initial task assignment during creation as well as reassignment. Emit no event for self-assignment, unchanged assignee, unassignment, or board moves. For mentions, emit once per distinct non-author recipient; event IDs differ between recipients.

Background workers publish committed records and await positive broker confirmation. Mark published only after confirmation and routing acceptance. Crash after confirmation but before recording it may duplicate publication; consumers must handle that. Failed/unroutable records remain pending.

Initial retry policy: exponential delay from 1 second, capped at 60 seconds, with jitter; keep retrying without discarding pending events. Alert/log when oldest pending record exceeds 5 minutes. Retain unpublished records until recovered. Retain confirmed outbox records at least 7 days for investigation; purge only those known published. Database storage limits and alert routing belong to infrastructure planning.

## Consumer processing

1. Validate schema/type/producer and authorized broker route. Malformed or unsupported messages go to the queue's dead-letter store, with sanitized diagnostic metadata.
2. Begin local database transaction; insert a unique processed-event marker keyed by `(consumer_name, event_id)` and apply the business effect together.
3. If the marker already exists, make no change. Otherwise commit marker and effect atomically.
4. Acknowledge only after commit. Crash before commit rolls back; crash after commit before acknowledgement replays harmlessly.

Notifications additionally enforce unique `(event_id, recipient_id)`. Create one notification with `read_at=null`; consumer may defensively suppress actor=recipient. Event timestamps populate `occurred_at`; database insertion time populates `created_at`. Recipient ID is stored privately and scoped by authenticated reads.

Transient processing failures retry after approximately 5s, 30s, 2m, 10m, and 30m; after the sixth failed processing attempt, route durably to a dead-letter queue. Never hot-loop immediate requeues. If dead-letter forwarding cannot be confirmed, retain the original message. Dead-letter messages are not automatically discarded; an operator investigates and replays with the original event ID after repair. Duplicate replay remains harmless. No claim of exactly-once transport is made.

Keep consumer deduplication markers and deletion/removal tombstones for the project lifetime. No retention-based replay beyond that supported lifetime is promised. Notifications are retained for the project lifetime too; deletion/archival endpoints are outside v1. Size storage and backups accordingly.

## Cleanup and delayed events

Task stores `assignee_membership_id` and the workspace version observed when validating that assignment. New membership after a leave/removal always receives a new ID. Member-removal consumers persist a tombstone for the removed membership and clear only tasks carrying that exact ID; never clear all tasks by user ID. New assignments after rejoining are unaffected by old events.

Workspace deletion persists a terminal tombstone, hides all child resources, and prevents later in-flight creations from committing after the tombstone. Cleanup and create/update transactions synchronize against these local tombstones. Authoritative REST checks deny all new access independently of event arrival. See [workflows](../workflows.md) for the check/commit race and snapshot reconciliation.

Historical notifications can arrive after a task is deleted, an invitation is answered, or membership is revoked. Keep the generic historical notification; clicking it rechecks authority through the owning REST service. Never infer access from an event or return copied protected content. Out-of-order assignment events represent different historical actions, not current task state; do not overwrite the task using them.

## Observability

Notification defines dashboards for pending outbox count/age, publish failures, consumed/failed events, duplicate suppression, retry count, dead-letter depth, and cleanup lag. Owners instrument their publishers/consumers. Gateway & Infrastructure exposes broker health and queue depth. Carry request_id and event_id in logs; redact credentials and avoid logging user content.
