# Google Sign-In and Application Sessions

This implements the challenge, exchange, logout, and current-user operations from the [Identity OpenAPI contract](../../../api_contracts/openapi/identity-workspace.openapi.json), following [authentication.md](../../../api_contracts/authentication.md) and [shared conventions](../../../api_contracts/conventions.md).

## Flow and persistence

1. The browser sends `POST /api/v1/auth/google/challenges` with an explicitly allowed Origin. Identity returns a UUID challenge ID, a 256-bit random nonce, and an expiry exactly five minutes after creation. Only the nonce hash, origin, and timing/consumption state are stored.
2. Google Identity Services uses the configured web client ID and this nonce in its popup/callback flow. The callback receives a Google ID token.
3. The browser sends JSON `{challenge_id, id_token}` to `POST /api/v1/auth/google/exchange`, from the same allowed origin that created the challenge.
4. Google's supported `google-auth` verifier checks signature, Google keys, issuer, configured audience, and issue/expiry timing. The service also checks strict claim types, subject, nonce, verified email/address, authorized party when present, and not-before when present. Display names become nonempty plain text of at most 200 code points, with `User` as fallback.
5. A conditional PostgreSQL update consumes an unexpired, unused, origin-bound challenge. Consumption, user upsert by `(google, subject)`, and session insertion share one transaction. A second concurrent/replayed exchange cannot consume it. Expiry is checked again after verification using the database clock.
6. The response contains a random 256-bit opaque application token, `token_type: Bearer`, an expiry exactly eight hours after creation, and a public user profile. Only its SHA-256 hash is stored. Google ID tokens are not persisted.

Emails are not unique identity keys; distinct Google subjects with the same verified email remain separate users. The public profile never exposes the Google subject. Existing users retain their UUID/creation time and refresh provider-derived name/email on a successful sign-in. Future workspace operations must maintain the stored `users.owned_workspace_count` transactionally; it starts at zero, and this task adds no workspace CRUD endpoints.

`GET /api/v1/me` requires the opaque application bearer token and performs a fresh database lookup. Unknown, expired, and revoked sessions return 401 `SESSION_INVALID`; missing Authorization returns 401 `AUTH_REQUIRED`. A database failure returns 503, without cached authorization.

`POST /api/v1/auth/logout` atomically revokes only the presented active session and returns bodyless 204. Repeated logout returns 401. Other sessions and the Google account remain signed in. The frontend clears its in-memory token even when logout returns 401. There is no refresh endpoint or application session cookie. Reload/expiry requires a fresh sign-in.

The first migration remains unchanged. `0002_google_sessions` adds users, challenges, sessions, and shared IP rate-limit buckets. Readiness expects the latest migration head and required table privileges; [workspace management](workspace-management.md) now adds `0003_workspace_management`. Database constraints enforce token-hash lengths, fixed lifetimes, provider-subject uniqueness, and the owned-workspace count bound. Failed user/session writes roll back challenge consumption.

## Google configuration

Real Google configuration is provisioned separately from CI. Create an OAuth client of type **Web application** in your Google project, configure its app/audience/test-user settings as applicable, and obtain its complete client ID. For local development, Google's setup instructions specify authorizing both `http://localhost` and `http://localhost:5173`; the helper itself runs at the latter origin. Use explicit HTTPS frontend origins in production. See [Google's setup instructions](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid) and [JavaScript nonce/callback configuration](https://developers.google.com/identity/gsi/web/reference/js-reference).

Set the following in ignored `.env.container` for Docker, or `.env` for host Python:

```dotenv
IDENTITY_GOOGLE_CLIENT_ID=REPLACE_WITH_WEB_CLIENT_ID.apps.googleusercontent.com
IDENTITY_ALLOWED_ORIGINS='["http://localhost:5173"]'
IDENTITY_GOOGLE_TIMEOUT_SECONDS=2
IDENTITY_AUTH_CHALLENGE_LIMIT_PER_MINUTE=10
IDENTITY_AUTH_EXCHANGE_LIMIT_PER_MINUTE=30
```

Replace the complete client-ID value with the actual value from your project. The same ID must be configured in the browser and backend. This popup/ID-token flow uses the public web client ID and Google's public verification keys; it needs no client secret in Identity. The exchange route accepts the documented JSON callback payload. It is not a redirect/form-post handler.

Existing environment files are preserved by setup; add missing Google settings locally. An empty/unset client ID leaves health and existing-session validation available while challenge/exchange return 503. Do not place real configuration, Google tokens, application tokens, or private key files in source control.

## Local browser verification

Complete the [README setup](../README.md#setup-and-local-verification), configure Google, and start the API/database with current migrations. Serve only the local helper directory in a second terminal, from the service root:

```powershell
python -m http.server 5173 --bind 127.0.0.1 --directory tools
```

Open `http://localhost:5173/google-signin.html`. The dedicated `tools` directory contains the [verification page](../tools/google-signin.html); environment and secret files are outside its served directory. The helper sets the referrer policy recommended by Google for localhost HTTP testing and uses the Google sign-in button popup/callback flow.

Enter the complete public web client ID, leave the local API URL at `http://127.0.0.1:8000`, and select **Create challenge**. Complete the Google sign-in button flow within five minutes. The page exchanges the callback token and displays only the current-user response, never either token. **Check current user** exercises `/api/v1/me`; **Log out of this application** exercises logout and clears page memory. Reload for a fresh challenge/sign-in; reloading also loses the application token by design.

For logout isolation, sign in separately in two browser tabs. Logging out in one tab must leave the other tab's session usable. Use the automated tests for expiry, invalid signatures/claims, replay/concurrency, and dependency failures rather than copying live tokens into command history or logs.

To check challenge connectivity without a Google credential:

```powershell
curl.exe -i -X POST http://127.0.0.1:8000/api/v1/auth/google/challenges -H "Origin: http://localhost:5173"
```

With the Google client ID configured and database ready, expect 201 plus nonce/challenge/expiry and no-store/request-ID headers. Use the helper for the token exchange instead of entering private tokens in terminal arguments. Stop the helper with Ctrl+C when finished.

## Failures and safe troubleshooting

| Result | Check |
| --- | --- |
| 403 `ORIGIN_NOT_ALLOWED` | Browser/script Origin must match the configured list; both login routes require it. Challenges also bind that exact origin. |
| 400/415/422 | Check JSON syntax/media type, UUID format, required fields, unknown properties, and token/body bounds. A challenge/logout/me request is bodyless. |
| 401 `AUTH_CHALLENGE_INVALID` | Challenge is missing, expired, consumed, or bound to another origin; create a fresh one. |
| 401 `GOOGLE_TOKEN_INVALID` | Check client-ID agreement, token claims/nonce, verified email, and clock synchronization; never print the token to diagnose it. |
| 401 `AUTH_REQUIRED` / `SESSION_INVALID` | Supply the application bearer token; sign in again after expiry/revocation/reload. |
| 429 `RATE_LIMITED` | Respect Retry-After. Challenge/exchange budgets are separate, per observed client IP, and shared through PostgreSQL. |
| 503 `DEPENDENCY_UNAVAILABLE` | Check client-ID configuration, database readiness/privileges, and bounded access to Google's public certificate endpoint. No session is created on verification failure. |

Google certificate responses are cached according to their HTTP cache headers. Requests and shared-session lock waits are bounded by `IDENTITY_GOOGLE_TIMEOUT_SECONDS`; invalid credentials and verification unavailability have distinct controlled errors. No tokeninfo dependency or refresh flow is used. The library reference is [Google's supported ID-token verifier](https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.id_token.html).

Inspect status/error code and X-Request-ID, then correlate the request ID with safe application logs. Log field allowlists exclude tokens, nonce values, email, provider payloads, SQL values, and raw exception details, including at DEBUG. Use `docker compose --profile app logs --tail 50 identity`; validate configuration with `docker compose --profile app config --quiet` rather than dumping runtime environment values.

The direct-service container disables proxy-header trust. Its rate limit uses the observed peer address, not arbitrary X-Forwarded-For. A future gateway deployment must establish trusted proxy forwarding explicitly before using forwarded client addresses; otherwise requests behind that proxy share its observed-IP budget.

## Automated verification

Run the three commands from [README tests](../README.md#test-and-ci-commands) or [testing.md](testing.md). All suites use Google verifier/key-transport doubles. An autouse fixture blocks the real requests HTTP transport, preventing accidental Google calls. Provider adapter tests use an ephemeral RSA key and mocked certificate response to exercise actual supported-library signature verification offline. No real Google project/token/secret is required by CI.

Unit tests cover all four endpoint/application operations, required/invalid claims, origin/bearer rules, safe errors, provider transport bounds, and lifecycle disposal. Request tests cover route methods, status/body/header contracts, malformed input, sessions/challenges, CORS, rate-limit errors, redaction, and provider/database failures. PostgreSQL integration tests cover fixed lifetimes/hash-only storage, subject identity/profile updates, duplicate email identities, eight concurrent HTTP exchanges, replay, concurrent user creation, expiry rechecks, transaction rollback, session revocation/isolation, shared rate limits, cleanup, and migration compatibility.

The existing [CI workflow](../../../.github/workflows/identity-workspace.yml) runs these nonempty suites before image build/smoke checks. This task's hosted run is obtained after the branch is pushed and a PR is opened. Real Google browser sign-in is separate manual verification with your provisioned project; the automated suite does not claim to have signed into a live Google account.

Local verification: 261 unit tests and 136 request tests passed on Windows and Linux; 47 PostgreSQL integration tests passed using the local Windows host and disposable real PostgreSQL, for 444 service tests with zero failures, errors, or skips. The built Linux/amd64 image passed its smoke test, including packaged authentication route guards, migration upgrade/repeat, database outage/recovery, DEBUG redaction, and shutdown cleanup. Shared contract validation and actionlint passed; the helper JavaScript passed syntax verification. Live Google account verification and this branch's hosted CI run remain separate steps.

## Maintenance

Expiry/revocation are enforced on every request independently of physical row cleanup. Run bounded cleanup periodically in the configured runtime to remove expired challenges, expired/revoked sessions, and rate buckets stale for over an hour. User records and active sessions are retained. Each invocation removes at most the requested limit per table and skips locked rows.

```powershell
# Host Python, using its ignored .env
& $identityUv run --locked python -m identity_workspace.auth.cleanup --limit 1000

# Docker, with the database already running and ignored .env.container configured
docker compose --profile app run --rm --no-deps identity python -m identity_workspace.auth.cleanup --limit 1000
```

Only row counts are printed. Scheduling this command belongs to deployment operations. See [database setup](database-setup.md), [health probes](health-probes.md), and [observability](observability.md) for the supporting foundation.
