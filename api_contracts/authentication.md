# Authentication and Authorization Contract

Design decision: Google proves identity; Identity & Workspace issues and owns opaque application sessions. This avoids distributing signing keys and permits immediate logout checks. It makes Identity a synchronous dependency for protected Task/Notification requests; return 503 rather than grant access if it is unavailable.

## Browser sign-in

1. Frontend calls `POST /api/v1/auth/google/challenges`. Identity returns `challenge_id`, random `nonce`, and expiry five minutes later. Store challenge server-side; use at least 256 random bits for the nonce.
2. Frontend initializes the Google Identity Services popup/callback flow with the configured OAuth web client ID and this nonce. The callback receives a Google ID token.
3. Frontend sends `{challenge_id, id_token}` to `POST /api/v1/auth/google/exchange` over HTTPS. Both login endpoints require an allowed `Origin`; scripted development clients must provide the configured local origin. The frontend associates the callback with its own pending challenge.
4. Identity & Workspace validates the Google token using the provider's supported verification library: signature/keys, issuer, configured audience, expiration, and matching nonce. Require `email_verified=true`; reject an expired or consumed challenge. Provider verification unavailable: 503, no session created. Never use tokeninfo as a production verification dependency.
5. Atomically consume the challenge, create/update the local user by `(provider=google, sub)`, and create the application session. Never merge accounts by email. Retain Google subject internally; do not return it in public profiles. Duplicate email identities must not be silently linked; invitation lookup returns 409 EMAIL_AMBIGUOUS if needed.
6. Return `{access_token, token_type:"Bearer", expires_at, user}`. The session token has at least 256 random bits; store only its cryptographic hash. Fixed expiry: eight hours; no refresh-token endpoint in MVP.

Frontend holds the application token in memory and sends it as `Authorization: Bearer <token>`. Reload/expiry requires a new Google sign-in; do not assume a persistent login. There is no application session cookie, so this design does not depend on third-party cookies between Netlify and the backend. The custom JSON callback flow is intentional: this is not Google's redirect/form-post handler, and clients must not submit arbitrary provider payloads to exchange.

`POST /api/v1/auth/logout` revokes the presented application session. Frontend clears memory even if it receives 401. Other active sessions and the user's Google account remain signed in. Frontend should disable automatic account selection for the current sign-out interaction. Losing the login response after challenge consumption requires a fresh sign-in challenge; do not replay the Google exchange as an idempotent create.

## Gateway boundary

The gateway forwards Authorization unchanged and strips client-supplied service credentials and identity assertion headers (`X-Service-Key`, `X-User-ID`, `X-User-Role`, and similar). It must not turn a supplied user ID into trusted identity. Identity validates its own session for every protected request. Task and Notification validate through the private APIs below. Gateway-level bearer presence checks are optional; they do not replace service authorization.

Allow only configured frontend origins; no wildcard production CORS. Set allowed headers Authorization, Content-Type, Idempotency-Key, If-Match, X-Request-ID; expose ETag, X-Request-ID, Retry-After. Browser preflight can complete without authentication. Allow configured local origins for development and an explicit Netlify deployment origin for production. Do not automatically trust arbitrary preview sites.

## Private service calls

| Caller | Private operation | Credentials |
| --- | --- | --- |
| Task | POST `/internal/v1/workspaces/{workspace_id}/access` | Task X-Service-Key + original user's bearer token |
| Task | POST `/internal/v1/auth/introspect` when workspace context is not applicable | Task X-Service-Key + user bearer |
| Notification | POST `/internal/v1/auth/introspect` | Notification X-Service-Key + user bearer |

Both credentials are required in the same request. Each caller gets a distinct high-entropy key, stored in deployment secrets. Identity maps the key to a service and checks its endpoint allowlist; Notification cannot call workspace access. Do not forward these headers to the browser. Service-key compromise must be handled by rotation; private network placement is additional protection, not authentication.

No user token or email is carried in broker events. Consumers use broker-specific service credentials and route ACLs; they do not require the original user's session to still be active. Service-to-service HTTP must stay on private networks; use TLS when crossing hosts unless an explicitly approved encrypted private transport protects it. Public traffic always uses TLS.

## Workspace access

For every Task API request, Identity validates session, active workspace, and active membership, then returns a consistent snapshot of actor/role and current members (at most 20). Task derives the actor from this response, not request fields. Assignee/mention targets must occur in that snapshot. No positive authorization caching or fallback to JWT claims. On Identity 401/404 propagate the corresponding public error; on timeout/transport/5xx return 503. A rejected service credential is an operational misconfiguration: log it and return public 503, not a request for the user to sign in again.

Owner-only board deletion is enforced by Task using `actor_role`. Identity & Workspace enforces workspace and membership permissions locally. Notification derives recipient ID from introspection and never accepts an arbitrary recipient filter. Data nesting must also be checked locally: task/board/comment belongs to the workspace in the path, including after moves/deletions.

Authorization occurs at the fresh check's database snapshot. A request already checked before revocation may finish; requests checked after revocation must fail. This explicit in-flight boundary avoids claiming a distributed transaction across services. Tombstones and reconciliation address delayed writes/cleanup; see workflows.md.

## Provider references

Google documents [server-side token verification](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token), including identity by `sub`, and the [GIS JavaScript nonce and callback options](https://developers.google.com/identity/gsi/web/reference/js-reference). Use those provider libraries and configuration requirements; the application session lifetime and private authorization protocol above are this project's design choices.
