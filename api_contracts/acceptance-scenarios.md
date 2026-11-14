# Implementation Acceptance Scenarios

These are contract-level tests to implement against running services, not claims that a backend already exists. Schema validation cannot prove transactionality, authorization, or delivery reliability.

| ID | Scenario | Expected result | Work areas |
| --- | --- | --- | --- |
| A01 | Valid Google token with matching fresh nonce | One user identity and an eight-hour app session | Identity & Workspace, Frontend |
| A02 | Invalid signature/audience/expiry/nonce or reused challenge | 401, no new session; never trust frontend user ID | Identity & Workspace |
| A03 | Logout then access each service using old session | 401; no mutation; no cached session acceptance | Identity & Workspace, Task, Notification |
| A04 | Service key without bearer, bearer without key, or Notification calling workspace access | Private request denied; public gateway never routes it | Identity & Workspace, Gateway & Infrastructure |
| A05 | Non-member guesses workspace/board/task/comment ID | 404, no data; current member calling owner-only action gets 403 | Identity & Workspace, Task |
| A06 | Identity unavailable during Task/Notification request | 503, no task mutation or protected response from stale cache | Task, Notification |
| W01 | Two concurrent creates while user owns nine workspaces | One success at most; other 409; owned total <=10 | Identity & Workspace |
| W02 | User joins someone else's workspace | Owned-workspace count unchanged | Identity & Workspace |
| W03 | Two concurrent board creates while workspace has four | One success at most; total <=5 | Task |
| W04 | Two acceptances while workspace has 19 members | One new membership; other remains pending with 409 | Identity & Workspace |
| I01 | Owner invites exact registered email; email case differs | Same registered recipient; one invitation/outbox record | Identity & Workspace |
| I02 | Unregistered, ambiguous, existing member, duplicate pending recipient | Documented 404/409; no partial invitation/event | Identity & Workspace |
| I03 | Accept repeats, then user leaves and old acceptance repeats | Same accepted invitation; no duplicate/new membership | Identity & Workspace |
| I04 | Owner cancels while recipient accepts | One terminal transition; loser returns 409; no orphan membership | Identity & Workspace |
| I05 | Workspace deleted before invitation response | 404; invitation cannot grant membership | Identity & Workspace |
| T01 | Minimal task create with title+board | Null optional fields, empty description, automatic number/metadata | Task |
| T02 | Unknown priority/status/custom field, overlength text, invalid dates | 422, no partial changes or events | Task, Identity & Workspace |
| T03 | PATCH one date makes resulting pair invalid | 422; both persisted dates unchanged | Task |
| T04 | Assign non-member or move to another workspace/deleted board | 422 without leaking destination details | Task |
| T05 | Move task to another active board in workspace | Stable task ID/number/comments, updated board/version, no notification | Task, Frontend |
| T06 | Self-assignment, unchanged assignment, unassignment | No assignment notification; null assignment works | Task, Notification |
| T07 | Two concurrent edits with same If-Match | One success; other 412; missing header 428 | Task, Identity & Workspace |
| T08 | Delete board containing tasks; reuse freed board slot | Old tasks/comments return 404; new board allowed | Task |
| C01 | Multiple mention ranges for one user, self-mention, literal @text | One event for distinct non-author selected user; none for literal/self | Task, Notification, Frontend |
| C02 | Emoji before mention, overlapping/out-of-range ranges, removed target | Correct Unicode offsets accepted; invalid ranges/member rejected | Task, Frontend |
| C03 | Attempt to edit/delete comment | No supported route; no data change | Task, Gateway & Infrastructure |
| N01 | Other user's notification ID or arbitrary recipient query | No other recipient data; 404 for ID, unsupported query rejected | Notification |
| N02 | Mark same notification read twice | Same read_at, count decremented once | Notification |
| N03 | Notification target deleted or membership revoked | Generic history remains; opening target returns 404 | Task, Identity & Workspace, Notification, Frontend |
| R01 | Lose successful create response and retry same key/body | Same original status/body/entity ID, one outbox set | All backend owners |
| R02 | Same key with different body; concurrent duplicate create | 409 conflict or in-progress; no duplicate entity | Identity & Workspace, Task |
| R03 | Reuse create key after resource becomes inaccessible | 404; cached private response not disclosed | Identity & Workspace, Task |
| E01 | Broker unavailable during task assignment/invitation | API succeeds after local commit; outbox remains pending | Identity & Workspace, Task, Gateway & Infrastructure |
| E02 | Broker restored after outage | Pending events published and notifications eventually appear | Identity & Workspace, Task, Notification, Gateway & Infrastructure |
| E03 | Producer crashes after publish confirmation before outbox marking | Duplicate delivery, one consumer effect | Identity & Workspace, Task, Notification |
| E04 | Consumer crashes before DB commit / after commit before ack | Retry yields exactly one business effect | Task, Notification |
| E05 | Same event replayed; unknown type or malformed event | Duplicate ignored; malformed message durably dead-lettered | Task, Notification, Gateway & Infrastructure |
| E06 | Remove member, rejoin with new membership ID, assign task, deliver old removal | New-generation assignment survives; old ones cleared | Identity & Workspace, Task |
| E07 | Old in-flight write reaches Task after removal/workspace tombstone processed | Cannot resurrect tombstoned membership/workspace state | Task |
| E08 | Task read snapshot older than a concurrent new-generation assignment | Refresh snapshot or 503; do not clear new assignment | Task, Identity & Workspace |
| E09 | Access check starts after removal but before cleanup event | 404 immediately; event delay grants no permission | Identity & Workspace, Task |
| E10 | Workspace deletion event repeated/delayed; outstanding task request | Workspace hidden from authority; cleanup idempotent; no resurrection | Identity & Workspace, Task |
| G01 | Route /workspaces/{id}/tasks through gateway | Goes to Task, not generic Identity route | Gateway & Infrastructure, Frontend |
| G02 | Inject X-User-ID/X-Service-Key or call /internal through gateway | Headers stripped; internal route unavailable | Gateway & Infrastructure |
| G03 | Wrong frontend origin, missing CORS headers, large payload | Correct 403/preflight behavior/413; permitted origin sees ETag | Gateway & Infrastructure, Frontend |
| O01 | Controlled consumer failure followed by recovery | Logs correlate request/event; dashboard shows retries/dead-letter/outbox lag | Notification, Gateway & Infrastructure, relevant owner |

## Product coverage

| Product requirement family | Contract coverage |
| --- | --- |
| AUTH | Public Google/session routes, private introspection/access, authentication.md; A01–A06 |
| WS/MEM | Workspace/member/invitation operations and lifecycle events; W01–W04, I01–I05, E06–E10 |
| BOARD/TASK | Task API, quota/version/idempotency rules; T01–T08 |
| COMMENT | CommentCreate ranges and immutable comments; C01–C03 |
| NOTIF/D-11 | Notification API, five event variants, outbox/consumer protocol; N01–N03, E01–E10 |
| INPUT | OpenAPI bounds plus semantic validation in conventions.md; T02–T03, C02 |
| DevOps | Private health probes, gateway rules, delivery metrics, failure demonstration; G01–G03, O01 |

## Work-area review checklist

Contract version: 1.0.0. Owner review is pending, not assumed from product approval.

- [ ] Identity & Workspace: Identity, private access, invitations, and Google/session policy.
- [ ] Tasks, concurrency, event publication, and cleanup races.
- [ ] Notification: Notifications, event schemas, retries, and deduplication.
- [ ] Gateway & Infrastructure: Gateway/private routing, broker capabilities, and deployment constraints.
- [ ] Frontend: Frontend flows, examples, auth UX, and error handling.
