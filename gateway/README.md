# API Gateway

Status: foundation in place (issue #28). Routing, header stripping, JSON 404/413 errors and request IDs work and are tested against mock upstreams. Throttling, CORS, upstream-failure mapping and metrics are still to do (see [Not yet implemented](#not-yet-implemented)).

This component exposes the public API entry point and routes requests to the three business services. It owns ingress configuration, not business entities or databases. Broker events bypass the gateway.

## Selected stack

| Item | Choice |
| --- | --- |
| Gateway software | Nginx, official `nginx:1.28-alpine` image |
| Configuration | One template, `nginx/templates/default.conf.template`, rendered with `envsubst` at container start |
| Container | `Dockerfile` in this folder; multi-arch base image (amd64 and arm64) |
| Tests | `pytest` + `requests` sending real HTTP requests to a running gateway; mock services in `tests/mock_upstream.py` |
| CI | `.github/workflows/gateway.yml` |

Why Nginx: every requirement in the [routing contract](../api_contracts/gateway-routing.md) maps to a built-in directive, so no plugins or custom code are needed. Caddy was rejected because rate limiting needs a plugin; Traefik because different limits per path are more awkward to express.

### Contract support

| Requirement | Nginx feature | Status |
| --- | --- | --- |
| Explicit routes by path family | regex `location` blocks (exact patterns, so the workspaces rule cannot swallow Task routes) | done, tested |
| Preserve method, path, query, body, Authorization, Idempotency-Key, If-Match | `proxy_pass` without a URI | done, tested |
| Strip client `X-Service-Key`, `X-User-ID`, `X-User-Role` and similar | `proxy_set_header <name> ""` | done, tested |
| Request ID (UUID) forwarded and returned | `$request_id` reformatted with a `map` | done, tested |
| Shared JSON error envelope for unknown paths (404) and oversized bodies (413) | `return` with a JSON body | done, tested |
| 64 KiB body limit | `client_max_body_size 64k` | done, tested |
| 10 s upstream timeout, no automatic retry | `proxy_*_timeout 10s`, `proxy_next_upstream off` | configured; the 503 mapping is not yet |
| Internal paths, metrics, broker admin never public | no matching route, falls through to 404 | done, tested |
| Per-IP throttles (10/min auth, 120/min other) with 429 + Retry-After | `limit_req_zone` / `limit_req` | not yet |
| CORS allow-list, preflight, exposed headers | `map` on `$http_origin` + `add_header` | not yet |
| 405 for unsupported methods | per-route method rules | not yet |
| 503 `DEPENDENCY_UNAVAILABLE` + Retry-After on upstream failure | `error_page 502 504` | not yet |
| Request-rate, latency, error metrics | `stub_status` + exporter, JSON access log | not yet |
| Public HTTPS | TLS termination at Nginx (Let's Encrypt/certbot on Oracle) | not yet |

Deployment architecture: one public Nginx container (the only published port); Identity, Task, Notification, databases and the broker stay on a private network. Upstream addresses are environment variables, so the same image runs locally, in tests and on Oracle.

ARM64: the base image publishes `linux/arm64`, and the CI job `container` builds the image for both architectures. Treat ARM64 as confirmed once that job has passed on `main`.

## Run and verify

Run from the repository root. Needs Docker with Compose, plus Python 3.13 for the tests.

```sh
# Build the image
docker build --tag gateway:local gateway

# Validate the rendered configuration
docker run --rm \
  -e IDENTITY_UPSTREAM=http://127.0.0.1:8000 \
  -e TASK_UPSTREAM=http://127.0.0.1:8001 \
  -e NOTIFICATION_UPSTREAM=http://127.0.0.1:8002 \
  gateway:local nginx -t

# Start the gateway with three mock upstreams (http://localhost:8080)
docker compose -f gateway/compose.test.yaml up -d --build --wait

# Run the request-level tests
python3 -m venv .venv && .venv/bin/pip install -r gateway/tests/requirements.txt
.venv/bin/python -m pytest gateway/tests -v

# Stop everything
docker compose -f gateway/compose.test.yaml down -v
```

pytest exits with an error if no tests are collected, so an empty suite fails CI.

To run the gateway against real services, set the upstream variables (see `.env.example`):

```sh
docker run --rm -p 8080:8080 \
  -e IDENTITY_UPSTREAM=http://host:8000 -e TASK_UPSTREAM=http://host:8001 \
  -e NOTIFICATION_UPSTREAM=http://host:8002 gateway:local
```

Upstream hostnames are resolved when Nginx starts, so start the services first (or use `depends_on` in Compose).

`GET /healthz` is the gateway's own liveness probe. It is not a business route and is not proxied; service `/health/*` paths are private and return 404 here.

## Not yet implemented

Tracked in the gateway issues under #4: routing details, throttling, CORS and 405/503 behaviour (#29); deployment and HTTPS (#32); automated image deployment (#33); metrics (#34).

## Handoff notes from Identity

Implement the [gateway routing contract](../api_contracts/gateway-routing.md) and [authentication boundary](../api_contracts/authentication.md): explicit routes, HTTPS/CORS, header stripping, correlation IDs, body limits, timeouts, and consistent errors. Keep private service endpoints, database ports, and broker administration inaccessible from public routes.

Identity's [private API implementation and handoff](../services/identity-workspace/docs/private-access-apis.md#gateway-and-infrastructure-handoff) provides per-service caller rules, secret configuration, consumer fixtures and a real deployment boundary verifier. Keep both `/internal/v1` routes outside public routing and strip client service/identity assertions. Run the [verification command](../services/identity-workspace/docs/private-access-apis.md#real-deployment-verification-command) against the actual gateway/private origins once implemented; the current direct service smoke test is not public-gateway evidence. Oracle provisioning and shared deployment configuration belong in `infra/`.
