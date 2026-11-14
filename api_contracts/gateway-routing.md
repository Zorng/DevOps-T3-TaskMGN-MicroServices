# Gateway Routing and Operations — Gateway & Infrastructure

Preserve method, path, query, JSON body, Authorization, Idempotency-Key, If-Match, and correlation ID. Do not rewrite domain payloads or expose internal exception responses. Public domain APIs remain under `/api/v1`.

| Path family | Upstream |
| --- | --- |
| `/api/v1/auth/**`, `/api/v1/me` | Identity & Workspace |
| `/api/v1/invitations/**` | Identity & Workspace |
| `/api/v1/workspaces` and `/api/v1/workspaces/{workspace_id}` | Identity & Workspace |
| `/api/v1/workspaces/{workspace_id}/members`, `/members/{user_id}`, `/membership`, `/invitations` | Identity & Workspace |
| `/api/v1/workspaces/{workspace_id}/boards` and descendants | Task |
| `/api/v1/workspaces/{workspace_id}/tasks` and descendants | Task |
| `/api/v1/notifications` and descendants | Notification |

Configure explicit/longest-match routes: a generic workspaces prefix must not swallow Task routes. Only expose operations in the public specs; unknown paths return the shared 404 envelope and unsupported methods 405 using the same Error schema. Health URLs appear in each spec for direct private service probing; they are not gateway upstream routes. Internal paths, metrics, broker admin tools, and database ports are not publicly forwarded.

Private upstream addresses/ports are deployment configuration. Suggested local aliases: identity-workspace:8000, task:8001, notification:8002. Public spec server URL represents the gateway for `/api/v1` routes. The same service responds to `/health/live` and `/health/ready` privately; metrics collection location is operational configuration.

## Responsibilities

- Terminate public HTTPS; isolate databases and broker on private networks.
- Configure CORS and header stripping exactly as authentication.md; forward response ETag and correlation headers.
- Enforce 64 KiB request-body bound and consistent JSON error envelope for gateway-generated failures.
- Propagate a UUID request ID. Redact Authorization, service keys, Google tokens, and personal content in access logs.
- Enforce upstream timeout of 10 seconds. Map failed connection/timeout to 503 DEPENDENCY_UNAVAILABLE, with Retry-After. Do not automatically retry POST/PATCH/DELETE.
- Initial throttles: challenge and exchange each 10 requests/minute per client IP; other public requests 120/minute per client IP. Return 429 with Retry-After. Configure/test these for the demo; do not trust spoofable forwarded-IP headers from untrusted clients. Services also enforce domain quotas; gateway throttles do not replace them.
- `OPTIONS` for recognized public routes returns CORS preflight without a session. Denied origins get 403; production origins are explicit configuration.
- Do not log service secrets or let client headers impersonate internal callers.
- Wire request-rate, latency, upstream errors, and availability metrics to Notification's dashboard.

## Per-service health semantics

`GET /health/live`: 200 `{ "status": "ok" }` if process is alive. No dependency checks.

`GET /health/ready`: 200 if required synchronous dependencies are usable, otherwise 503 `{ "status": "unavailable" }`. Identity requires its database; Task/Notification require their databases and Identity availability. An unavailable broker alone does not fail readiness while the outbox safely accepts pending messages. Export broker/outbox health separately so that this degradation is visible.

No authentication on private health endpoints. Restrict reachability at the network/gateway. Readiness checks must be inexpensive; do not use real user sessions, create data, or perform a real Google login.
