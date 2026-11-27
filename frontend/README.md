# Frontend

Status: framework and test pipeline in place (#46), gateway API client and Google sign-in (#47), workspace, membership and invitation screens (#48), and board screens (#49), task screens (#50), comments with mentions (#51), and notifications (#52). Deployment verification (#53) follows.

The web app implements the screens and journeys in the [product specification](../docs/product-specification.md) against the gateway's [REST contracts](../api_contracts/README.md). It calls the public gateway (`/api/v1/...`) only. It never calls private service APIs, the message broker, or a database, and no backend credentials or private service keys belong in this directory.

## Stack

| Concern | Choice |
| --- | --- |
| Language and UI | TypeScript, React 19 |
| Routing | React Router |
| Build and dev server | Vite (dev origin fixed at `http://localhost:5173`) |
| Unit and component tests | Vitest, jsdom, React Testing Library, user-event |
| Mocked API tests | MSW (Mock Service Worker) running in Node |
| Hosting | Netlify (static build output in `dist/`) |

Why this stack:

- Vite produces a static bundle that Netlify can serve directly, with no server runtime.
- Vitest reuses the Vite configuration, so tests need no second toolchain.
- MSW lets screens be built and tested against contract-shaped responses while the backend services are unfinished.
- React Router handles client-side navigation; Netlify rewrites every path to `index.html` so reloads on deep links work.

Design constraints taken from `api_contracts/authentication.md`: the application session token is held in memory only (no cookies, no `localStorage`), so a reload or expiry requires a new Google sign-in.

Mock responses exist only in tests (`src/test/`). The running application never imports MSW and does not present mock data as live backend integration.

## Implemented and not yet implemented

Implemented (#46):

- Project scaffold, locked dependencies, and public configuration placeholders.
- Application shell with navigation, a skip link, and placeholder routes (home, workspaces, notifications, not found).
- Reusable loading, empty, and error components with accessible roles and keyboard operation. No screen uses them yet.
- Test tooling with a mocked gateway, and CI for type check, tests, and production build.

Implemented (#47):

- Gateway API client (`src/api`): bearer header, `Idempotency-Key` on creates with safe retry of the same key, `If-Match` and ETag handling for updates and deletes, cursor pagination, and mapping of the shared error envelope to typed errors with user-facing messages.
- Google sign-in (`src/auth`): challenge, Google Identity Services popup, credential exchange, in-memory session with expiry handling, sign-out, and a protected-route guard.
- The home page loads the signed-in user's profile and owned-workspace count from `GET /me`.

Implemented (#48), described in [Workspaces, membership and invitations](#workspaces-membership-and-invitations):

- Workspace list, creation, opening, renaming and deletion.
- Member list, leaving, and owner removal of members.
- Owner invitations (send, list, cancel) and the recipient's invitation inbox (list, accept, decline).

Implemented (#49), described in [Boards](#boards):

- Board list with creation inside each workspace, the five-board limit, and an empty-workspace state.
- Board page with navigation between the workspace's boards, rename (owner and member) and owner-only deletion with confirmation.

Implemented (#50), described in [Tasks](#tasks):

- Task list per board (paginated), creation, opening, editing, assigning, moving between boards, and deletion with confirmation.

Implemented (#51), described in [Comments and mentions](#comments-and-mentions):

- Comment list on the task page (paginated, oldest first) and comment creation with an explicit member picker for mentions.

Implemented (#52), described in [Notifications](#notifications):

- Notification list with unread count, unread-only filter, cursor pagination, manual refresh and individual mark-as-read; links to the invitation or task they concern.

Not yet implemented (later sub-issues):

| Area | Sub-issue |
| --- | --- |
| Netlify deployment and verification of the complete user journey against the real gateway | #53 |

Drag-and-drop task moves and automatic notification polling are optional features and are not required by the project scope.

## Requirements

- Node.js 22 or newer (see `.nvmrc`; MSW 3 requires Node 22+).
- npm (installed with Node).

## Commands

Run all commands from the `frontend/` directory.

```sh
npm install          # first time only; updates package-lock.json (commit it)
npm ci               # reproducible install from the lockfile (used by CI)
cp .env.example .env.local   # then edit the values (see Public configuration)
npm run dev          # development server at http://localhost:5173
npm run typecheck    # TypeScript check, no output files
npm test             # run all tests once; fails if no tests are found
npm run test:watch   # re-run tests on change
npm run build        # type check, then production build into dist/
npm run preview      # serve dist/ locally at http://localhost:4173
```

To reproduce what CI runs: `npm ci && npm run typecheck && npm test && npm run build`.

## Public configuration

Vite embeds every `VITE_*` variable in the browser bundle, so these values are public. Do not put secrets, service keys, or backend credentials in them.

| Variable | Meaning |
| --- | --- |
| `VITE_API_BASE_URL` | Gateway base URL without a trailing slash. Defaults to `http://localhost:8080`. The local stack publishes the gateway on `http://127.0.0.1:8080`, so use that value in `.env.local`. |
| `VITE_GOOGLE_CLIENT_ID` | Google OAuth web client ID. It must be the same client ID that Identity & Workspace validates as the token audience. No client secret is used. |

`.env.example` documents both variables; real `.env*` files are git-ignored.

Allowed origins: the gateway CORS configuration and the Google OAuth client must both allow `http://localhost:5173` (local development) and the production Netlify site URL.

## Running against the local stack

To sign in for real, run the backend stack and the frontend together.

1. From the repository root, start the stack as described in [`infra/README.md`](../infra/README.md) (`python3 infra/scripts/setup_env.py`, then `docker compose -f infra/compose.yaml up -d --build --wait`).
2. Put the Google OAuth client ID in `infra/.env` as `IDENTITY_GOOGLE_CLIENT_ID`, then run `docker compose -f infra/compose.yaml up -d --wait`. Without it, the sign-in endpoints answer `503` and the sign-in page shows "service temporarily unavailable".
3. In `frontend/.env.local`, set `VITE_API_BASE_URL=http://127.0.0.1:8080` and `VITE_GOOGLE_CLIENT_ID` to the same client ID.
4. Run `npm run dev` and open `http://localhost:5173`. Restart the dev server after changing `.env.local`.

The Google client must list `http://localhost:5173` as an authorized JavaScript origin, and while the Google project is in testing mode your Google account must be a test user. To try invitations you need a second Google account that has signed in to the app once, because invitations target registered users by exact email.

## Workspaces, membership and invitations

Routes (all require sign-in; a signed-out visitor is sent to `/sign-in` and returned afterwards):

| Route | What it does |
| --- | --- |
| `/workspaces` | Lists owned and joined workspaces (paginated, "Load more"), creates a workspace, and explains the owned-workspace limit. |
| `/workspaces/:workspaceId` | Workspace page: members, and for the owner rename, delete and invitations. |
| `/invitations` | The signed-in user's own invitations: filter by status, accept or decline. |

Rules the screens follow (from `docs/product-specification.md` and `api_contracts/`):

- **Limits.** A user owns at most 10 workspaces; joined workspaces do not count, and the page shows the count from `GET /me`. A workspace has at most 20 members including the owner. Pending invitations do not reserve member slots, so accepting at capacity is refused with the invitation left pending.
- **Roles.** The owner can rename and delete the workspace (after an explicit confirmation), remove other members, and invite or cancel invitations. A member can view the workspace and leave it. The owner cannot leave; the page tells them to delete the workspace instead. The backend stays authoritative: the UI hides controls a role cannot use, and still handles a `403` or `404` if the role changed meanwhile.
- **Names and emails.** Names are trimmed and limited to 100 Unicode code points; invitation emails to 254. The server's per-field validation messages are shown when it rejects a value.
- **Stale versions.** Rename and delete send `If-Match` with the ETag from the last read. On `412 VERSION_CONFLICT` the page reloads the latest version, keeps what the user typed, and asks them to review before trying again. A member change also changes the workspace version, so the page re-reads the workspace after one.
- **Safe creates.** Creating a workspace or an invitation uses one `Idempotency-Key` per attempt. After an unclear failure (network error, `503`) pressing the button again reuses the same key, so no duplicate is created.
- **Invitations.** The owner invites by exact registered email. The screens explain an unknown email (`USER_NOT_FOUND`), an ambiguous email (`EMAIL_AMBIGUOUS`), an existing member (`ALREADY_MEMBER`), a duplicate pending invitation (`INVITATION_ALREADY_PENDING`), a full workspace (`MEMBER_LIMIT_REACHED`) and an invitation that already reached a final state (`INVITATION_NOT_PENDING`). Accept and decline need no key because repeating them is safe.
- **Lost access.** If a workspace is deleted or the user is removed, the next request returns `404` and the page switches to "Workspace not available" with a link back to the list.

Components: `WorkspacesPage`, `WorkspacePage`, `MembersSection`, `InvitationsSection` (owner) and `MyInvitationsPage` live in `src/workspaces/`, next to their API functions (`workspacesApi.ts`, `invitationsApi.ts`) and tests.

## Boards

| Route | What it does |
| --- | --- |
| `/workspaces/:workspaceId` | The workspace page also lists the workspace's boards and creates new ones. |
| `/workspaces/:workspaceId/boards/:boardId` | One board: navigation between the workspace's boards, rename, and (owner) delete. It lists the board's tasks and creates new ones (see [Tasks](#tasks)). |

Rules the screens follow (from `docs/product-specification.md` and `api_contracts/`):

- **Boards are statuses.** A board is a named task status. There are no fixed statuses and no columns inside a board. A workspace with no boards is shown as empty; the app never creates "Todo", "In Progress" or "Done" for the user.
- **Limit.** At most five boards per workspace. The list shows "n of 5 boards" and disables creation at five, but the server stays authoritative: `409 BOARD_LIMIT_REACHED` (for example when another member created the fifth board first) shows a message and re-reads the list.
- **Names.** Trimmed, 1 to 50 Unicode code points, checked before sending; the server's per-field messages are shown when it rejects a name.
- **Roles.** Owners and members create and rename boards. Only the owner sees Delete. If a refusal (`403`) or a disappearance (`404`) arrives anyway, the page re-reads the workspace and the board and shows what is true now.
- **Deleting.** The owner confirms in a panel that says the board and every task and comment in it are removed and cannot be recovered, and that a board slot is freed. Cancel is focused first and focus returns to the Delete button on cancel. Delete sends `If-Match` with the ETag from the last read of the board. After success the user returns to the workspace with a notice.
- **Stale versions.** Rename and delete send `If-Match`. On `412 VERSION_CONFLICT` (or an unclear result) the page reloads the latest board, keeps the typed name, and asks the user to try again; a delete needs a fresh confirmation against the new version.
- **Safe creates.** One `Idempotency-Key` per create attempt; pressing the button again after an unclear failure reuses it, so no duplicate board is created.
- **Navigation.** The address holds both the workspace and the board, so reloads and links keep the active workspace and board; the board navigation marks the current one. A deleted or hidden board shows "Board not available" with a link back to the workspace; a hidden workspace shows "Workspace not available"; a `403` shows "Access denied".

Components: `BoardsSection` and `BoardPage` live in `src/boards/`, next to `boardsApi.ts` and their tests.

## Tasks

| Route | What it does |
| --- | --- |
| `/workspaces/:workspaceId/boards/:boardId` | The board page lists the board's tasks (paginated, "Load more tasks"), creates tasks, and moves a task to another board from the list. |
| `/workspaces/:workspaceId/tasks/:taskId` | One task: details, comments, edit, move and delete. |

Rules the screens follow (from `docs/product-specification.md` and `api_contracts/`):

- **Fixed fields only.** Title (required, 1 to 200 characters), destination board (required), description (optional, up to 5000 characters, plain text), assignee (optional), start date and due date (optional, calendar dates). The task number, creator, created and updated times, and version are shown read-only. There is no priority, no custom field and no separately editable status: a task's board is its status.
- **Validation.** Titles are trimmed; lengths are counted in Unicode code points; descriptions are kept exactly as typed. A start date after the due date is refused before sending (equal dates are allowed). The server's per-field messages are shown next to the field when it rejects a value.
- **Assignees.** The selector offers only current workspace members, read from `GET /workspaces/{id}/members`. If the server refuses an assignee (for example the person was removed meanwhile), the reason is shown and the member list is read again. A task whose assignee is no longer a member shows that person as "(no longer a member)" rather than silently clearing them.
- **Edits use PATCH with omission and null.** Only changed fields are sent; an omitted field stays as it is, `null` clears the assignee or a date. Title, description and board are never cleared. An unchanged assignee is never re-sent, so no repeated assignment is triggered. If nothing changed, nothing is sent.
- **Moving.** A destination selector lists the workspace's other boards. Only `board_id` is sent, so identity, number, description, assignee and dates are untouched. A destination that was deleted meanwhile shows the server's reason and re-reads the boards.
- **Stale versions.** Edit, move and delete send `If-Match`. On `412 VERSION_CONFLICT` (or an unclear result) the page reloads the latest task, keeps the typed text, and asks the user to try again; a delete needs a fresh confirmation against the new version.
- **Safe creates.** One `Idempotency-Key` per create attempt. After an unclear failure the same key is reused when the button is pressed again, and the list is read again from the server after a create, so a retry never shows the task twice.
- **Deleting.** A confirmation explains that the task and all its comments are removed and cannot be recovered. Cancel is focused first. After success the user returns to the task's board with a notice.
- **Lost access.** A deleted task or board shows "Task not available"; a removed member (or deleted workspace) sees "Workspace not available"; a `403` shows "Access denied". The backend stays authoritative for every action, whatever the controls showed.

Components: `TasksSection`, `TaskForm` and `TaskPage` live in `src/tasks/`, next to `tasksApi.ts` and their tests.

## Comments and mentions

Comments live on the task page (`/workspaces/:workspaceId/tasks/:taskId`).

- **Add and read only.** Comments are listed oldest first as the contract orders them, with "Load more comments". There are no edit or delete controls, because comments are immutable in this MVP. After a comment is added the list is read again from the server; with more than one page, use "Load more comments" to reach the newest.
- **Plain text.** Authors, bodies and mention names are rendered as text, never as HTML, so markup typed into a comment is shown literally. Line breaks are kept.
- **Explicit mentions.** The only way to mention someone is the "Mention a member" picker, which lists the workspace's current members. "Insert mention" puts `@Name` at the caret and records a structured range for that member. Typing `@Dara` by hand is ordinary text and mentions nobody. Mentions can be removed from the list under the box; the text stays.
- **Code point offsets.** A range's `start` and `length` count Unicode code points in the exact body, so an emoji before a mention counts as one, not two. The body is sent exactly as typed and is never trimmed, because trimming would shift the offsets. The same counting is used to draw stored comments, and a stored range that does not fit or does not start with `@` is ignored (the comment is shown as plain text).
- **Ranges stay aligned.** Whenever the text changes, the changed region is found and ranges before it stay, ranges after it move by the same number of characters, and a range that was touched is dropped because its text is no longer exactly `@Name`.
- **Bounds checked before sending.** 1 to 2000 characters (a whitespace-only comment is refused), at most 50 ranges, at most 20 different people, no overlapping ranges, and only current members. The picker refuses an insertion that would break a bound.
- **Backend stays authoritative.** If a mentioned person was removed meanwhile, the server's `422` reason is shown with a prompt to update the mentions, the members and task are read again, and sending is blocked locally until the mention is removed. A deleted task or revoked access (`404`) shows "Task not available" or "Workspace not available".
- **Safe retry.** One `Idempotency-Key` per comment. After an unclear failure (network error, `503`) pressing "Add comment" again reuses the same key, so no duplicate is created. Changing the text starts a new attempt.

Components: `CommentsSection` lives in `src/comments/`, next to `commentsApi.ts`, the pure mention logic in `mentions.ts`, and their tests.

## Notifications

The `/notifications` page lists notifications addressed to the signed-in user, newest first (`GET /notifications`).

- **Types.** Each notification is an invitation, an assignment or a mention. The wording comes from the type alone ("invited you to join a workspace", "assigned you a task", "mentioned you in a comment") plus the actor's name and the time. The contract's notifications are generic: they carry no task title, comment text or invitation email, and the page shows none.
- **Unread count.** Shown at the top and always read from `GET /notifications/unread-count`. The page never adds or subtracts it locally: after "Mark as read" it asks the server again, so repeated clicks, failures, or notifications that arrived meanwhile cannot make the number wrong. If the count cannot be refreshed, the last known number is kept and marked as possibly out of date.
- **Filtering and pagination.** "Show unread only" sends `unread_only=true` and starts a new list. "Load more notifications" follows the server's opaque cursor.
- **Manual refresh.** The list does not update by itself (automatic polling is optional and not implemented). "Refresh" reads the list and the count again. A failed refresh shows an error with a retry.
- **Mark as read.** Only unread items have the button. Nothing is sent for an item that is already read or while its request is running. The server's response replaces the item, and a failed request leaves the item and the count as they were. Opening a notification does not mark it read.
- **Opening a target.** Invitation notifications open `/invitations/:invitationId`; assignment and mention notifications open the task page. A notification never grants access: those pages ask the server again, and a deleted or inaccessible target shows "Invitation not available" or "Task not available" (with a link back for invitations) while the notification stays in the list as history.
- **Session expiry.** A `401` signs the user out and returns to the sign-in page, like every other screen.

Components: `NotificationsPage` lives in `src/notifications/` next to `notificationsApi.ts` and its tests. The invitation target page is `src/workspaces/InvitationPage.tsx`.

## Tests

- Test files sit next to the code they cover, named `*.test.ts` or `*.test.tsx`.
- `src/test/setup.ts` starts an MSW server for every test with `onUnhandledFrame: 'error'`, so a request without a mock handler fails the test and tests never reach a real gateway.
- `src/test/handlers.ts` holds the default mock responses, shaped like the shared error envelope in `api_contracts/conventions.md`. Override them per test with `server.use(...)`. Base new mocks on `api_contracts/examples/`.
- `src/test/fixtures.ts` builds contract-shaped workspaces, members, invitations, boards, tasks, comments, notifications and error bodies. `src/test/renderApp.tsx` renders the whole app with a signed-in or signed-out session and a fake Google, so screen tests drive real routes against mocked responses.
- Component tests query by accessible role and name, and keyboard behavior is tested with user-event.
- Running `npm test` also writes a JUnit report to `test-results/junit.xml` (git-ignored).

## Continuous integration

`.github/workflows/frontend.yml` runs on pull requests, pushes to `main`, and manual dispatch. It installs locked dependencies with `npm ci`, type-checks, runs the tests, uploads `test-results/` as a build artifact (even when tests fail), and runs the production build with placeholder public values. Any failing step fails the job, and a run with no tests fails.

## Netlify deployment

`netlify.toml` defines the build command (`npm run build`), publish directory (`dist`), Node version, and the single-page-app rewrite. In the Netlify site settings:

1. Set the base directory to `frontend`.
2. Define `VITE_API_BASE_URL` (the public HTTPS gateway URL) and `VITE_GOOGLE_CLIENT_ID`.
3. Add the resulting site URL to the gateway CORS allowlist and to the Google OAuth client's authorized JavaScript origins.

## Layout

```text
frontend/
  index.html            Vite entry page
  vite.config.ts        build, dev server, and test configuration
  netlify.toml          Netlify build settings
  .env.example          public configuration placeholders
  src/
    main.tsx            entry point (router, config, auth provider)
    App.tsx             route definitions
    config.ts           public configuration reader
    api/                gateway client, error mapping, idempotent creates, versioned updates, pagination
    auth/               session store, sign-in service, Google Identity Services loader, auth provider
    components/         app shell, route guard, auth controls, loading, empty and error states
    pages/              home, sign-in and placeholder pages
    workspaces/         workspace, member and invitation screens with their API functions
    boards/             board list, board page and board API functions
    tasks/              task list, form, task page and task API functions
    comments/           comment list and form, mention logic and comment API functions
    notifications/      notification list, read state and notification API functions
    test/               test setup, MSW server, default handlers, fixtures, render helper
```
