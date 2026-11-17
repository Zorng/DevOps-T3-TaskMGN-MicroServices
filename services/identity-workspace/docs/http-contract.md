# Identity & Workspace HTTP Contract Foundation

Shared HTTP handling now follows [REST conventions](../../../api_contracts/conventions.md), the Error schema in [Identity OpenAPI](../../../api_contracts/openapi/identity-workspace.openapi.json), and the [origin/header policy](../../../api_contracts/authentication.md#gateway-boundary). Health dependency failures retain the separate Health response described in [health probes](health-probes.md).

## Request processing

The pure ASGI middleware assigns one correlation UUID per request and enforces the 65,536-byte body limit before a route handler or its dependencies run. It counts actual received bytes, including streamed requests without Content-Length, and stops buffering as soon as the limit is exceeded. Declared oversized bodies are rejected immediately. Malformed/duplicate Content-Length values or a declared/actual mismatch are 400.

For a matched operation, the middleware discovers allowed query parameters and body expectations from FastAPI's effective route metadata, including prefixed/nested routers and dependency parameters. Unknown routes and unsupported methods keep 404/405 behavior. Trailing slashes are not automatically redirected to another route.

| Condition | Response |
| --- | --- |
| Body over 64 KiB | 413 `PAYLOAD_TOO_LARGE` |
| Body on a bodyless operation | 400 `MALFORMED_REQUEST` |
| Unsupported query parameter | 400 `MALFORMED_REQUEST` |
| Malformed JSON, invalid UTF-8, or non-JSON NaN/Infinity literal | 400 `MALFORMED_REQUEST` |
| Nonempty body without application/json, unsupported charset, or unsupported content encoding | 415 `UNSUPPORTED_MEDIA_TYPE` |
| Unknown JSON properties, invalid schema fields, or missing required body | 422 `VALIDATION_FAILED` |
| Missing route / unsupported method | 404 `NOT_FOUND` / 405 `METHOD_NOT_ALLOWED` |
| Disallowed browser origin | 403 `ORIGIN_NOT_ALLOWED` |
| Unexpected handler/response-validation error | 500 `INTERNAL_ERROR` |

Bodyless operations do not require Content-Type. `application/json` can specify UTF-8; compressed bodies are not implemented. The size guard runs before route input validation. For matched operations, unsupported query parameters are rejected before checking body format, and a bodyless operation rejects any nonempty body before media-type checks.

The only real service routes currently implemented are the health probes. Body-bearing examples in tests are test-only routes, not authentication/workspace API stubs.

## Correlation, errors, and response headers

Every application response, including middleware rejection, preflight, 404/405, and internal errors, returns `X-Request-ID` and `Cache-Control: no-store`. A valid canonical incoming UUID is retained (uppercase normalized); missing/invalid/ambiguous IDs receive a UUIDv4. The middleware stores the same identifier in request state and a request-scoped ContextVar for application/observability use. It resets that context at request completion, including disconnects and errors.

Errors have this shape:

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "The request contains invalid fields.",
    "request_id": "00000000-0000-4000-8000-000000000012",
    "details": [{"field": "name", "reason": "Exceeds the permitted text length."}]
  }
}
```

Details is always an array. Validation diagnostics use declared field names and controlled reasons, never raw input, custom validator messages, unknown property names, or arbitrary dictionary keys. Up to 50 details are returned; this limits diagnostics rather than truncating user content. Framework HTTP exception detail and unexpected exception messages are never copied into responses. This layer does not log request bodies, credentials, or exception text.

Future domain handlers should raise `APIError` with an appropriate contract status/code and controlled public text. Conflicts require an explicit code such as `WORKSPACE_LIMIT_REACHED`; a bare HTTPException cannot identify the correct domain conflict and is treated as an internal programming error. Do not construct intentional public errors from raw provider/driver messages. `ErrorDetail` and the response models enforce the contract's text bounds.

General dependency and rate-limit errors include a positive Retry-After value (default two seconds). The Health 503 body stays `{"status":"unavailable"}`. Method errors preserve only valid HTTP methods in Allow; authentication errors use the application's Bearer challenge. Resource authorization and domain-specific decisions belong to their child tasks.

## Strict JSON model helpers

Future JSON request models should inherit `ContractModel` from `src/identity_workspace/validation.py`. It forbids unknown properties, disables string-to-number/boolean coercion, rejects non-finite numeric fields, and hides submitted values in the text representation of validation errors. Typed HTTP query parameters still use FastAPI's normal query-string parsing.

Reusable field types include:

- `WorkspaceName`: trims surrounding whitespace, then enforces 1-100 Unicode code points.
- `InvitationEmailText`: trims and enforces 1-254 Unicode code points, retaining case/dots/plus aliases. Email-format checking and case-insensitive lookup belong to the invitation implementation; this helper supplies only normalization/bounds.
- `MemberSearchText`: maximum 100 code points.
- `UUIDValue`: accepts canonical UUID JSON strings without relaxing numeric/boolean strictness.
- `CalendarDate`: accepts real YYYY-MM-DD dates, including valid leap dates.

The nonblank helper preserves the original text when checking whitespace-only content, allowing future comment-like inputs to retain offsets. No user-authored value is silently truncated. The field declarations preserve snake_case JSON keys. Follow-up [session handling](google-signin.md) and [workspace management](workspace-management.md) now implement authentication/authorization, name validation, PATCH semantics, idempotency, pagination and the ownership quota using these conventions.

## CORS configuration

`IDENTITY_ALLOWED_ORIGINS` is a JSON list in the service's ignored `.env`, for example:

```dotenv
IDENTITY_ALLOWED_ORIGINS='["http://localhost:5173","http://127.0.0.1:5173"]'
```

An empty/unset list allows requests without Origin (health checks and service clients) and denies requests carrying an Origin. Entries must be explicit origins with no wildcard, user information, path, query, or fragment. HTTP is allowed only for loopback development origins; production entries must use HTTPS. Configure the exact deployed frontend origin, such as `https://your-site.netlify.app`, and explicitly select any approved previews. CORS is not a replacement for session/workspace authorization.

Allowed origins receive Access-Control-Allow-Origin with their exact origin, Vary: Origin, and exposed ETag, X-Request-ID, and Retry-After headers. Wildcard/credentialed CORS response headers cannot be introduced by individual handlers. Application sessions use bearer headers rather than cookies.

OPTIONS preflight succeeds with 204 only for an implemented public `/api/v1/` operation, supported method, and permitted headers. It runs without executing authentication dependencies or the business handler. Allowed request headers are Authorization, Content-Type, Idempotency-Key, If-Match, and X-Request-ID, alongside standard CORS-safelisted headers. Private health/internal paths are not public preflight targets. Invalid origins receive 403; unknown paths/methods and unsupported requested headers use the shared error format.

Step 8 adds the local origin list to the existing ignored development `.env` only when absent; credentials and existing origin configuration are preserved. `.env.example` supplies the same placeholder local configuration for new checkouts.

## Try the implemented routes

Start the application using [application startup](application-startup.md). From another PowerShell terminal:

```powershell
curl.exe -i http://127.0.0.1:8000/health/live
curl.exe -i "http://127.0.0.1:8000/health/live?unknown=example"
curl.exe -i -X POST http://127.0.0.1:8000/health/live
curl.exe -i http://127.0.0.1:8000/missing
curl.exe -i -H "Origin: http://localhost:5173" http://127.0.0.1:8000/health/live
```

Expected statuses: 200, 400, 405, 404, and 200 (with the configured local origin). Error request IDs match their response headers. Public business routes remain unavailable until their features are implemented.

## Tests and remaining work

Use the separate unit/request commands in [application startup](application-startup.md#verification-commands) and the [PostgreSQL integration command](database-setup.md#integration-tests). Coverage includes exact byte limits and streams, declared-length mismatches, malformed/media-type failures, strict/nested field handling, aliases/dependency queries, safe errors, concurrent request ID isolation, CORS headers, and preflight without a session. Health and persistence tests remain part of verification.

Step 8 recorded 124 passing unit tests, 68 passing request tests, and 24 passing PostgreSQL integration tests, with zero skips. Real localhost HTTP smoke checks also verified health success, malformed query, unsupported method, missing route, allowed/denied origin, and oversized body responses, including shared headers and matching error IDs. The temporary smoke-test server was shut down afterward. No new dependency installation was required. Step 9 adds [structured logs and metrics](observability.md); the service container/smoke test and CI remain subsequent scaffold steps. The original service README is retained.

Related documents: [contract checklist](contract-requirements.md), [health probes](health-probes.md), [application startup](application-startup.md), and [stack decisions](stack-decisions.md).
