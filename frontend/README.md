# Frontend

Status: framework and test pipeline in place (#46), gateway API client and Google sign-in working (#47). The workspace, board, task and notification screens follow in #48 onward; their routes are placeholders.

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
- The home page loads the signed-in user's profile and workspace count from `GET /me`.

Not yet implemented (later sub-issues; the current routes are placeholders):

| Area | Sub-issue |
| --- | --- |
| Workspace, membership, and invitation screens | #48 |
| Board navigation and management | #49 |
| Task creation, editing, assignment, and board moves | #50 |
| Task comments with the member mention picker | #51 |
| Notification list and read-state controls | #52 |
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

The Google client must list `http://localhost:5173` as an authorized JavaScript origin, and while the Google project is in testing mode your Google account must be a test user.

## Tests

- Test files sit next to the code they cover, named `*.test.ts` or `*.test.tsx`.
- `src/test/setup.ts` starts an MSW server for every test with `onUnhandledFrame: 'error'`, so a request without a mock handler fails the test and tests never reach a real gateway.
- `src/test/handlers.ts` holds the default mock responses, shaped like the shared error envelope in `api_contracts/conventions.md`. Override them per test with `server.use(...)`. Base new mocks on `api_contracts/examples/`.
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
    test/               test setup, MSW server, default handlers, render helpers
```