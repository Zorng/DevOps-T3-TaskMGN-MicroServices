# Task Management Application — Product Specification

Status: Product baseline confirmed — ready for API design  
Version: 1.1  
Date: 2026-11-14  
Team: Five computer science students, DevOps course

## 1. Purpose

Build a small collaborative task management application with a simple frontend and independently deployable microservices. Users organize work into workspaces and boards, collaborate on tasks, and receive relevant notifications.

The project must also demonstrate the DevOps workflow required by [the assignment brief](<../_notes/DevOps_Project 2026.pdf>): service-owned databases, REST communication, containers, CI/CD, published images, cloud deployment, centralized logging, and metrics monitoring.

The team will finalize product behavior first, then API contracts, and then implement services. Service owners may choose different technology stacks. This specification confirms service responsibilities but does not prescribe languages, frameworks, or database products.

## 2. Decision status

### Confirmed direction

- Task management is the application domain.
- Include a simple frontend.
- Authenticate through Google sign-in; enforce workspace authorization in the application.
- Users can own up to 10 active workspaces; joined workspaces do not count toward this limit.
- Each workspace can contain up to five taskboards.
- A workspace has one creator and can include other users as members.
- Tasks have a fixed set of basic fields inspired by GitHub Projects; custom fields are not supported.
- Tasks include a start date instead of priority.
- Each board represents a task status; moving a task between boards changes its status.
- Notify users about invitations, mentions in tasks, and task assignments.
- Notifications are in-app only; self-assignment and self-mentions do not generate notifications.
- Notification failures preserve successful originating changes; pending delivery is retained for retries, duplicate events produce only one notification, and failures are logged. Cross-service changes must enforce the agreed access-revocation and assignment-cleanup rules.
- Product specification precedes API design and implementation.
- Service owners may use different technology stacks behind agreed contracts.
- Invitations target registered users only. Workspace roles are owner and member.
- Mentions are supported only in comments; comments are add/read only.
- Deletion, leaving, and member-removal rules and the invitation lifecycle below are confirmed.
- All text inputs have the confirmed length limits and validation rules in section 6.
- Each workspace supports at most 20 members, including its owner.
- The three services in section 9 are confirmed.
- Use a hybrid architecture: REST for frontend requests and synchronous access checks; broker-delivered events for notifications and cross-service cleanup, with transactional outboxes, acknowledgements, retries, and duplicate handling.
- Hosting targets are Oracle Cloud Always Free for backend services and Netlify free-tier for the frontend.

### Baseline and next-phase decisions

The product defaults and supporting behavior in this document are confirmed. This includes the permission matrix, task-field requiredness, exact-email invitation lookup, member mention picker, text limits, date validation, plain-text content, manual notification refresh, individual mark-as-read, and no notifications for board moves or unassignment.

Each workspace supports at most 20 members, including its owner. API mechanisms, infrastructure allocation, and team scheduling remain next-phase decisions; they do not block this product baseline.

## 3. Users and outcomes

| User | Desired outcome |
| --- | --- |
| Workspace owner | Create a collaboration space, invite teammates, and manage membership. |
| Workspace member | View shared work, create tasks, update progress, and collaborate through comments. |
| Invited user | Understand an invitation and accept or decline it. |
| Signed-in user | Find their workspaces and read relevant notifications. |

An owner is also a workspace member. Roles apply separately in each workspace; owning one workspace gives no privileges in another.

## 4. MVP scope

### Included

- External sign-in and sign-out.
- Workspace creation, listing, renaming, and deletion.
- Workspace membership and invitations.
- Board creation, listing, renaming, and deletion.
- Basic task creation, viewing, editing, movement between boards, and deletion.
- Task comments with explicit user mentions.
- In-app notifications and unread/read state.
- A simple web interface supporting the core journeys.

Product behavior below is confirmed; technical delivery and coordination mechanisms will be specified during API design.

### Excluded from the initial scope

- Local password registration and password recovery.
- Email, SMS, and mobile push notifications.
- Public workspaces, anonymous access, and shareable invitation links.
- Custom roles, multiple owners, and ownership transfer.
- Custom fields, separate status columns within boards, subtasks, dependencies, attachments, and recurring tasks.
- Multiple assignees and cross-workspace task moves.
- Real-time collaborative editing and mandatory WebSocket updates.
- Advanced search, analytics, billing, and mobile applications.

## 5. Product concepts

| Concept | Meaning |
| --- | --- |
| User | A local application identity linked to an external sign-in identity. |
| Workspace | A private collaboration boundary containing members and boards. |
| Membership | A user's role in a workspace: owner or member. |
| Invitation | An offer to join one workspace, addressed to one recipient. |
| Board | A named task status within one workspace, containing the tasks currently in that status. |
| Task | A unit of work belonging to exactly one board at a time; its board determines its status. |
| Comment | A message attached to a task, optionally containing explicit mentions. |
| Notification | A recipient-specific record of an invitation, assignment, or mention. |

These concepts describe product behavior, not a shared database schema. Service ownership is confirmed in section 9; each service will design its own database.

## 6. Functional requirements and acceptance criteria

### AUTH — Authentication and authorization

Confirmed behavior:

- AUTH-01 (confirmed): Users sign in through Google. Other providers and local passwords are outside the MVP.
- AUTH-02: Successful first sign-in creates a local user profile; repeat sign-in with the same provider identity returns the same user.
- AUTH-03: Users can sign out of the application.
- AUTH-04: Backend APIs enforce authentication and workspace permissions independently of the frontend.
- AUTH-05: Users can access only workspaces they belong to, their own invitations, and their own notifications.

Acceptance criteria:

- A signed-out visitor cannot read or modify private application data.
- A non-member cannot access a workspace, board, task, or comment by guessing its identifier.
- A member cannot perform owner-only actions by directly calling an API.
- Failed or cancelled external sign-in does not grant access.

External sign-in establishes identity. Application authorization still determines workspace permissions. Account linking across multiple providers is outside the MVP.

### WS — Workspaces

- WS-01: A signed-in user can create a workspace with a non-empty name.
- WS-02: The creator becomes its owner and initial member.
- WS-03 (confirmed): A user may own at most 10 active workspaces. Joining someone else's workspace does not count toward this limit.
- WS-04: Users can list the workspaces they own or belong to.
- WS-05: The owner can rename or delete a workspace.
- WS-06: Deletion requires explicit confirmation in the frontend.

Acceptance criteria:

- Creating an eleventh owned workspace fails with a clear explanation, including when requests occur concurrently.
- A rejected creation leaves no partial workspace or membership.
- Deleting an owned workspace frees one slot.
- A deleted workspace and its boards/tasks are no longer accessible. Pending invitations cannot be accepted afterward.

Physical cleanup and cross-service deletion coordination will be designed later; the user-visible access rule above must hold.

### MEM — Membership and invitations

Confirmed behavior:

- MEM-01 (confirmed): Two roles exist: owner and member. A workspace has exactly one owner.
- MEM-02: Only the owner can invite users or remove other members.
- MEM-03 (confirmed): Invitations target existing registered users by exact email match. Discovery exposes only the information needed to identify that recipient, not a browsable directory.
- MEM-04: An invitation has one of these states: pending, accepted, declined, or cancelled.
- MEM-05: Only the intended recipient can accept or decline a pending invitation.
- MEM-06: Accepting creates membership once. Declining does not create membership.
- MEM-07: An owner can cancel a pending invitation. Automatic expiry is excluded from the MVP.
- MEM-08: Existing members cannot be invited again. At most one pending invitation exists for the same workspace and recipient.
- MEM-09: Members can leave a workspace. The owner cannot leave without deleting the workspace because ownership transfer is excluded.
- MEM-10: Removed or departed members lose workspace access, and their task assignments are cleared. Their previous comments remain attributed to them.
- MEM-11: A workspace may have at most 20 members, including the owner. Pending invitations do not reserve member slots. Accepting an invitation at capacity is rejected clearly and leaves the invitation pending; the recipient can try again after a slot becomes available.

Acceptance criteria:

- Creating an invitation produces an invitation notification for its recipient.
- Repeating an acceptance does not create duplicate membership.
- Cancelled, declined, or accepted invitations cannot transition to another outcome. A new invitation may be issued when appropriate.
- A recipient cannot accept an invitation to a deleted workspace.
- Removing or leaving a workspace prevents subsequent authorized reads and writes there.
- Concurrent invitation acceptances cannot exceed the 20-member limit. A rejected acceptance creates no membership and does not mark the invitation accepted.

Invitations to users who have never signed in are outside the MVP.

### BOARD — Taskboards

Confirmed behavior:

- BOARD-01: Each workspace may contain at most five active boards.
- BOARD-02: Owners and members can create and rename boards with a non-empty name.
- BOARD-03 (confirmed): Each board represents one task status. Board names such as Todo, In Progress, and Done are examples, not a fixed status enum. There are no separate status columns within a board.
- BOARD-04: Only the owner can delete a board, with explicit confirmation.
- BOARD-05: Deleting a board removes access to its tasks and comments and frees one board slot.

Acceptance criteria:

- Creating a sixth board fails clearly, including under concurrent requests.
- Members can view every board within their workspace.
- Boards from another workspace are inaccessible to non-members.

### TASK — Tasks

The field set is fixed: users cannot add custom fields. Start date replaces priority, and board membership determines status. Requiredness and field details below are confirmed.

| Field | Required | Behavior |
| --- | --- | --- |
| Title | Yes | Non-empty text. |
| Description | No | Plain text; empty by default. |
| Board (status) | Yes | One board in the task's workspace; changing boards changes status. No independently editable status field. |
| Start date | No | Calendar date without a time of day. |
| Assignee | No | At most one current workspace member; unassigned by default. |
| Due date | No | Calendar date without a time of day. |
| Task number | Automatic | Human-readable identifier unique within the workspace; never reused. |
| Creator | Automatic | User who created the task. |
| Created/updated time | Automatic | Recorded by the application. |

- TASK-01: Owners and members can create, view, and edit tasks in workspace boards.
- TASK-02: Owners and members can move a task to another board within the same workspace. The destination board becomes its status. Moving back to an earlier board is allowed.
- TASK-03: Owners and members can delete tasks after confirmation.
- TASK-04: Tasks cannot be assigned to non-members.
- TASK-05: Changing the assignee to another user generates an assignment notification for the new assignee.
- TASK-06: Saving an unchanged assignee does not produce another assignment notification. Unassigning does not notify the previous assignee in the MVP.

Acceptance criteria:

- A task can be created using only its title and destination board.
- Invalid destination boards, dates, or assignees are rejected without partial updates. Confirmed date rule: when both dates are supplied, start date must be on or before due date.
- An assignee can be cleared, and a task can be moved back from a Done board to another board.
- A deleted task cannot be opened or modified through a stale link.
- Moving a task preserves its identity, task number, description, assignee, dates, comments, and existing notification links. Its board and updated time change.
- A task belongs to exactly one board after a successful move. A failed move leaves it in its original board.
- Moves to deleted boards or boards in another workspace are rejected. Moving to the current board has no additional effect.
- Board moves do not generate notifications in the MVP.
- Custom fields are not accepted as task fields.

### COMMENT — Comments and mentions

Confirmed behavior:

- COMMENT-01: Workspace members can read and add non-empty plain-text comments to a task.
- COMMENT-02: An @mention picker selects current workspace members using stable user identifiers.
- COMMENT-03: Submitting a comment notifies each distinct mentioned member once.
- COMMENT-04: Typing an unselected display name alone is not a mention.
- COMMENT-05 (confirmed): Comment editing and deletion are excluded from the initial MVP.
- COMMENT-06 (confirmed): Mentions are supported only in comments, not in task descriptions.

Acceptance criteria:

- Mentioning the same user several times in one comment produces one notification.
- A non-member cannot be selected as a mention recipient.
- A comment is rejected if a selected mention is no longer a member when submitted, with a clear prompt to update the selection.

### NOTIF — Notifications

Confirmed behavior:

- NOTIF-01 (channel confirmed): Support in-app invitation, task assignment, and task comment mention notifications. Email, SMS, and mobile push notifications are outside the MVP.
- NOTIF-02: A user can list only their own notifications, newest first, with unread state and an unread count.
- NOTIF-03: A user can mark an individual notification as read. Repeating the action has no additional effect.
- NOTIF-04: Notifications identify the event, actor, relevant workspace/task, and creation time.
- NOTIF-05: An invitation notification opens the invitation; assignment and mention notifications open the relevant task when access is still permitted.
- NOTIF-06 (confirmed): Self-assignment and self-mentions do not notify the acting user.
- NOTIF-07: Notifications do not grant access. If a target is deleted or access is revoked, opening it shows an unavailable/access-denied state without returning protected content.
- NOTIF-08: Refreshing the notification list is sufficient for the MVP; automatic polling is optional.

Acceptance criteria:

- A newly generated notification starts unread and contributes to the count.
- Marking it read decreases the unread count once.
- Retrying delivery of the same event does not create duplicate notifications.
- A temporary notification failure does not falsely report that a successfully saved task or invitation failed. Delivery must be retryable, and failures must be visible in logs.

D-11 behavior is confirmed: preserve a successful originating task or invitation change when notification delivery fails, retain pending delivery for later retries, prevent duplicate notifications for the same event, and log failures for investigation. Cross-service changes must also enforce the agreed lifecycle rules, including revoking access and clearing assignments when a member is removed.

Transactional outboxes, broker-delivered events, acknowledgements, retries, and duplicate handling are confirmed in section 9. Broker selection, delivery timing, retry limits, event schemas, and detailed coordination protocols remain for technical design. No specific latency guarantee is committed.

### INPUT — Text limits and validation

Confirmed requirement: every user-controlled text input must have a reasonable length limit enforced by backend APIs and reflected in the frontend. The following concrete limits are confirmed:

| Input | Minimum | Maximum |
| --- | --- | --- |
| Workspace name | 1 | 100 characters |
| Board name | 1 | 50 characters |
| Task title | 1 | 200 characters |
| Task description | 0 | 5,000 characters |
| Comment body | 1 | 2,000 characters |
| Invitation email lookup | 1 | 254 characters, plus email-format validation |
| Member/mention search, if implemented | 0 | 100 characters |

Confirmed shared rules:

- Count Unicode code points consistently across service languages; do not rely on language-specific string length behavior.
- Trim surrounding whitespace from names, titles, and email lookup values before validation. Reject whitespace-only required text, including comments.
- Reject over-limit input with a field-specific error; do not silently truncate user content.
- Descriptions and comments remain plain text and are rendered safely.
- Any additional text input introduced during API design must receive an explicit limit before implementation.
- Start and due dates must be valid calendar dates. When both are present, start date must be on or before due date.
- Workspaces allow at most 20 members including the owner. Structured mention payload limits will be specified during API design within the current-member-only rule.

## 7. Confirmed permissions matrix

All permissions refer to the user's role in the target workspace.

| Action | Owner | Member | Non-member |
| --- | --- | --- | --- |
| View workspace, members, boards, tasks, comments | Yes | Yes | No |
| Rename/delete workspace | Yes | No | No |
| Invite users/cancel invitations/remove members | Yes | No | No |
| Leave workspace | No | Yes | N/A |
| Create/rename boards | Yes | Yes | No |
| Delete boards | Yes | No | No |
| Create/edit/delete tasks | Yes | Yes | No |
| Move tasks between boards in the same workspace | Yes | Yes | No |
| Assign tasks to current workspace members | Yes | Yes | No |
| Add comments/mention members | Yes | Yes | No |

Invitation recipients can view and respond to their own invitations before joining. Notification recipients can view and mark their own notifications as read. Neither exception grants access to workspace content.

## 8. Frontend scope and user journeys

Minimum screens:

1. Sign-in page.
2. Workspace list with create action and owned-workspace count.
3. Workspace page listing boards and members, with owner controls.
4. Workspace board navigation and a board view listing tasks in that status, with an action to move a task to another board.
5. Task detail view or dialog with fields and comments.
6. Invitation detail with accept/decline actions.
7. Notification list with unread indicators.

The frontend should show loading, empty, validation, permission, and service-error states. Moving between boards may use a destination-board dropdown; drag-and-drop is optional. A usable desktop layout is the initial target.

Primary demonstration journey:

1. User A signs in and creates a workspace with boards named Todo, In Progress, and Done.
2. User B signs in so they are available for the invitation flow.
3. User A invites User B.
4. User B opens the invitation notification and accepts.
5. User A creates a task in Todo, optionally sets its start and due dates, and assigns it to User B.
6. User B sees the assignment notification and moves the task to the In Progress board.
7. User B mentions User A in a comment; User A sees the mention notification.
8. The task is moved to the Done board.

## 9. Architecture and API planning boundaries

Confirmed service decomposition:

| Service | Product data and behavior owned |
| --- | --- |
| Identity & Workspace | User profiles and external identity mappings, workspaces, memberships, invitations. |
| Task | Boards, tasks, comments, task assignments, and mention references. |
| Notification | Notification records, recipient read state, and duplicate-delivery handling. |

Each service must own its database. No service may read or write another service's tables. Cross-service data references use identifiers and agreed APIs.

### Confirmed REST and event-driven communication

| Flow | Communication |
| --- | --- |
| Frontend → API gateway → business services | REST |
| Task service → Identity & Workspace service for current workspace access/membership checks | REST |
| Invitation, task assignment, and comment mention → Notification service | Events through a message broker |
| Member removal/departure and workspace deletion → Task service cleanup | Events through a message broker |

The gateway handles browser-facing API routing. Events travel directly between service publishers, the broker, and service consumers; they do not pass through the gateway. The broker is shared infrastructure, not an additional business service or business database.

Initial event scope (names are descriptive, not finalized schemas):

| Event | Producer | Consumer and purpose |
| --- | --- | --- |
| InvitationCreated | Identity & Workspace | Notification: notify the invited user. |
| TaskAssigned | Task | Notification: notify the new assignee, excluding self-assignment. |
| TaskCommentMentioned | Task | Notification: notify distinct mentioned members, excluding the author. |
| WorkspaceMemberRemoved | Identity & Workspace | Task: clear assignments for a removed or departed member. |
| WorkspaceDeleted | Identity & Workspace | Task: clean up the workspace's boards, tasks, and comments. |

Reliability requirements:

- Save each originating business change and its pending event together in the producer's database transaction (transactional outbox).
- A producer-owned background publisher sends pending events and retries failures. Use persistent broker storage, durable delivery configuration, and publisher confirmation before considering publication complete.
- Consumers acknowledge a message only after its processing succeeds. Failed processing is retried; repeated failures must be visible and recoverable under a policy to be defined during technical design.
- Treat delivery as at least once. Assign stable event IDs and make consumers idempotent so redelivery does not duplicate notifications or repeat harmful cleanup effects. Do not claim exactly-once delivery.
- Keep successful originating changes when notification delivery is unavailable. Log failures and expose delivery health through observability.
- Membership/access revocation takes effect at the authoritative service immediately; asynchronous assignment cleanup must not grant continued access. If current access cannot be verified, deny access or return an unavailable response rather than authorizing from stale state.
- Deleted workspaces and their contents remain inaccessible while physical cleanup runs asynchronously.
- Technical design must cover event ordering, delayed delivery, rejoining a workspace before old cleanup arrives, and stale notification targets.

Broker product, event schemas, queue/routing topology, retry/backoff settings, dead-letter handling, retention, and outbox implementation details remain technical decisions. REST integration and event integration must both be demonstrated.

Before implementation, the team must finalize:

- Detailed contracts across the confirmed service boundaries and responsibility for permission checks.
- External sign-in integration, identity propagation, and internal service authentication.
- REST endpoint contracts with request/response examples and validation rules.
- Identifier formats, timestamps, date handling, errors, pagination, and versioning conventions.
- Invitation, assignment, and mention notification payloads and duplicate-event identifiers.
- Behavior during timeouts, retries, partial failures, and unavailable dependencies.
- Workspace/board deletion, membership removal, assignment cleanup, and access revocation across services.
- Health checks, environment configuration, structured logs, metrics, and container expectations.
- Mock responses and integration acceptance scenarios so services can be developed independently.

Different technology stacks are permitted; they do not change externally agreed behavior or contracts. No specific endpoint or implementation library is finalized by this document.

## 10. DevOps and academic deliverables

The assignment requires:

- At least 2–3 independent microservices; this project uses three.
- REST APIs and demonstrated service-to-service communication; each service communicates with at least one other service.
- A separately owned database for each service.
- A Dockerfile per service and Docker Compose for local execution.
- A CI/CD pipeline that runs tests and builds container images.
- Images published to Docker Hub or another registry.
- Deployment to a cloud or hosting platform.
- Centralized logs and an example of using them to investigate an error.
- Metrics monitoring and dashboards showing relevant application or infrastructure behavior.
- A concise report of approximately 4–5 pages, including source, container image, and deployed application links.
- A 10-minute presentation and demonstration covering the problem, architecture, implementation, DevOps workflow, and observability.
- Every team member understanding the overall architecture and their contribution.

Project quality criteria:

- A new teammate can run the application using documented setup instructions.
- Secrets are supplied through configuration and are not committed to source control.
- Logs allow a cross-service operation or failure to be traced without exposing credentials.
- Tests cover permission boundaries, workspace/board limits, invitation transitions, and core cross-service journeys.
- A controlled service failure can be demonstrated and investigated through the observability tools.

### Confirmed hosting targets

- Backend services: Oracle Cloud Always Free.
- Frontend: Netlify free-tier.

Oracle Cloud replaces the previously selected Render backend hosting target. Backend services will run in containers on Oracle Cloud virtual machines. Each service retains its own database and deployment boundary even when multiple services share a VM.

Proposed starting topology: one VM hosting the three service stacks and observability components. Splitting across multiple VMs remains an option after checking available resources and actual workload requirements. The VM count, shapes, region, database placement, and observability placement are not yet finalized.

These are selected deployment targets, not a provisioned or verified deployment design. During infrastructure planning, verify current Always Free eligibility and capacity, allocate storage for databases and logs, and plan private service communication, HTTPS ingress, backups, and deployment access. If ARM-based VMs are selected, all service and infrastructure container images must support ARM64. Record constraints affecting availability or the demonstration before committing to the topology. No paid resources are approved by this specification.

## 11. Decision register

| ID | Decision area | Decision or proposed default | Status |
| --- | --- | --- | --- |
| D-01 | Authentication provider | Google sign-in only | Confirmed |
| D-02 | Invitation recipients | Existing registered users only, found by exact email lookup | Confirmed |
| D-03 | Workspace limit | 10 owned active workspaces; joined workspaces excluded | Confirmed |
| D-04 | Roles and permissions | Owner and member, with permissions in section 7 | Confirmed |
| D-05 | Task fields and status model | Fixed fields; title and board required; description, start date, due date, and one assignee optional; metadata automatic; board determines status | Confirmed |
| D-06 | Mention location and selection | Comments only, using a picker of current workspace members | Confirmed |
| D-07 | Notification channel and self-events | In-app only; suppress self-assignment/self-mention | Confirmed |
| D-08 | Deletion, leaving, and removed-member behavior | Rules in WS, MEM, BOARD, and TASK | Confirmed |
| D-09 | Comment editing/deletion | Add/read only for the MVP | Confirmed |
| D-10 | Invitation lifecycle | Explicit accept/decline/cancel; no automatic expiry | Confirmed |
| D-11 | Delivery reliability and cross-service coordination | Preserve successful originating changes; retain pending notifications for retries; deduplicate delivery; log failures; enforce agreed cross-service lifecycle rules | Behavior and event-driven approach confirmed; detailed protocols pending technical design |
| D-12 | Input limits and validation | Text limits in section 6; start date ≤ due date when both exist; maximum 20 workspace members including owner | Confirmed |
| D-13 | Service decomposition | Identity & Workspace, Task, Notification | Confirmed |
| D-14 | Hosting and project logistics | Oracle Cloud Always Free backend; Netlify free-tier frontend. Deadline, checkpoints, and team ownership not yet supplied | Hosting targets confirmed; logistics open |
| D-15 | Communication architecture | Hybrid REST and broker-delivered events; transactional outboxes, acknowledgements, retries, and idempotent consumers | Confirmed; broker and detailed contracts pending technical design |

## 12. Completion gates

### Product specification ready — achieved

- The project lead has confirmed the MVP product decisions and supporting defaults.
- Permissions, lifecycle rules, task fields, validation limits, and acceptance criteria define the product baseline.
- API design can proceed; team scheduling and infrastructure allocation remain separate planning work.

### API contracts ready

- Service ownership and cross-service workflows are agreed.
- Contracts document success, validation, authorization, and failure cases.
- Shared conventions and integration examples are available to every service owner.
- Contract changes have an agreed review process.

### MVP ready for demonstration

- The main user journey works through the frontend on the deployed system.
- Limits and permission checks work through direct API requests as well as the UI.
- CI tests/builds pass and published images correspond to the deployed version.
- Centralized logs and metrics are available, including an error-investigation demonstration.
- Report links, architecture diagram, and presentation are prepared and rehearsed by all five members.

## 13. Change log

| Version | Date | Change |
| --- | --- | --- |
| 0.1 | 2026-11-14 | Initial draft from the project brief and product discussion; proposed defaults separated from confirmed direction. |
| 0.2 | 2026-11-14 | Confirmed fixed task fields; replaced priority with start date; made boards represent status and enabled task moves between boards within a workspace. |
| 0.3 | 2026-11-14 | Recorded Google sign-in, registered-user invitations, owned-workspace limit, two roles, comment-only mentions, deletion rules, comment restrictions, invitation lifecycle, three services, and hosting targets; proposed concrete text limits. |
| 0.4 | 2026-11-14 | Replaced Render with Oracle Cloud Always Free for backend hosting; retained Netlify for the frontend; left VM allocation and detailed deployment topology pending infrastructure planning. |
| 0.5 | 2026-11-14 | Confirmed D-07: in-app notifications only, with no notifications for self-assignment or self-mentions. |
| 0.6 | 2026-11-14 | Confirmed D-11 reliability and cross-service coordination behavior; implementation mechanisms remain for API design. |
| 1.0 | 2026-11-14 | Confirmed remaining product defaults, permission matrix, task-field requiredness, invitation lookup, mention picker, text/date validation, 20-member workspace cap, and supporting notification behavior; marked product baseline ready for API design. |
| 1.1 | 2026-11-14 | Confirmed hybrid REST/event-driven architecture, initial event scope, outbox-based reliable publishing, consumer acknowledgements/retries, and duplicate handling; broker choice remains open. |
