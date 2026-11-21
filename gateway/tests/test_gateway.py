"""Request-level tests: send real HTTP requests to a running gateway."""
import os
import re
import subprocess
import time

import pytest
import requests

BASE = os.environ.get("GATEWAY_URL", "http://localhost:8080")
COMPOSE_FILE = os.path.join(os.path.dirname(__file__), "..", "compose.test.yaml")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
WS = "11111111-1111-4111-8111-111111111111"
# The frontend dev origin the gateway allows by default (see CORS_ALLOWED_ORIGINS).
ORIGIN = "http://localhost:5173"


def call(method, path, origin=ORIGIN, **kwargs):
    """Send a request. Browser requests carry an Origin; pass origin=None to omit it."""
    headers = dict(kwargs.pop("headers", {}))
    if origin is not None:
        headers.setdefault("Origin", origin)
    return requests.request(method, BASE + path, timeout=10, headers=headers, **kwargs)


def compose(*args):
    subprocess.run(["docker", "compose", "-f", COMPOSE_FILE, *args], check=True)


@pytest.mark.parametrize(
    "method,path,service",
    [
        ("POST", "/api/v1/auth/google/challenges", "identity"),
        ("POST", "/api/v1/auth/google/exchange", "identity"),
        ("POST", "/api/v1/auth/logout", "identity"),
        ("GET", "/api/v1/me", "identity"),
        ("GET", "/api/v1/invitations", "identity"),
        ("GET", "/api/v1/invitations/abc", "identity"),
        ("POST", "/api/v1/invitations/abc/response", "identity"),
        ("POST", "/api/v1/invitations/abc/cancel", "identity"),
        ("GET", "/api/v1/workspaces", "identity"),
        ("POST", "/api/v1/workspaces", "identity"),
        ("GET", f"/api/v1/workspaces/{WS}", "identity"),
        ("PATCH", f"/api/v1/workspaces/{WS}", "identity"),
        ("DELETE", f"/api/v1/workspaces/{WS}", "identity"),
        ("GET", f"/api/v1/workspaces/{WS}/members", "identity"),
        ("DELETE", f"/api/v1/workspaces/{WS}/members/u1", "identity"),
        ("DELETE", f"/api/v1/workspaces/{WS}/membership", "identity"),
        ("GET", f"/api/v1/workspaces/{WS}/invitations", "identity"),
        ("POST", f"/api/v1/workspaces/{WS}/invitations", "identity"),
        ("GET", f"/api/v1/workspaces/{WS}/boards", "task"),
        ("POST", f"/api/v1/workspaces/{WS}/boards", "task"),
        ("GET", f"/api/v1/workspaces/{WS}/boards/b1", "task"),
        ("GET", f"/api/v1/workspaces/{WS}/tasks", "task"),
        ("PATCH", f"/api/v1/workspaces/{WS}/tasks/t1", "task"),
        ("POST", f"/api/v1/workspaces/{WS}/tasks/t1/comments", "task"),
        ("GET", "/api/v1/notifications", "notification"),
        ("GET", "/api/v1/notifications/unread-count", "notification"),
        ("POST", "/api/v1/notifications/n1/read", "notification"),
    ],
)
def test_route_goes_to_expected_service(method, path, service):
    r = call(method, path)
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == service
    assert body["method"] == method
    assert body["path"] == path  # path and query are not rewritten


def test_query_string_is_preserved():
    r = call("GET", f"/api/v1/workspaces/{WS}/tasks?status=open&limit=5")
    assert r.json()["path"] == f"/api/v1/workspaces/{WS}/tasks?status=open&limit=5"


def test_authorization_and_conditional_headers_forwarded_unchanged():
    headers = {
        "Authorization": "Bearer abc.def",
        "Idempotency-Key": "key-123",
        "If-Match": '"v3"',
    }
    seen = call("PATCH", f"/api/v1/workspaces/{WS}/tasks/t1", headers=headers).json()["headers"]
    assert seen["authorization"] == "Bearer abc.def"
    assert seen["idempotency-key"] == "key-123"
    assert seen["if-match"] == '"v3"'


def test_client_supplied_identity_headers_are_stripped():
    spoofed = {
        "X-Service-Key": "stolen",
        "X-User-ID": "admin",
        "X-User-Role": "owner",
        "X-User-Email": "a@b.c",
        "X-Actor-Role": "owner",
    }
    seen = call("GET", "/api/v1/me", headers=spoofed).json()["headers"]
    for name in spoofed:
        assert name.lower() not in seen


def test_request_id_is_a_uuid_forwarded_and_returned():
    r = call("GET", "/api/v1/me")
    forwarded = r.json()["headers"]["x-request-id"]
    assert UUID.match(forwarded)
    assert r.headers["X-Request-ID"] == forwarded


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/api/v1/unknown",
        "/internal/v1/auth/introspect",
        f"/internal/v1/workspaces/{WS}/access",
        "/health/live",
        "/health/ready",
        "/metrics",
        f"/api/v1/workspaces/{WS}/unknown",
        "/api/v1/me/extra",
        "/api/v1/invitations/abc/accept",
        f"/api/v1/workspaces/{WS}/boards/b1/tasks",
    ],
)
def test_unknown_and_private_paths_return_json_404(path):
    r = call("GET", path)
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "NOT_FOUND"
    assert UUID.match(err["request_id"])
    assert err["details"] == []


@pytest.mark.parametrize(
    "method,path,allow",
    [
        ("GET", "/api/v1/auth/logout", "POST"),
        ("DELETE", "/api/v1/me", "GET"),
        ("PUT", f"/api/v1/workspaces/{WS}", "GET, PATCH, DELETE"),
        ("PATCH", "/api/v1/notifications", "GET"),
        ("GET", f"/api/v1/workspaces/{WS}/membership", "DELETE"),
        ("DELETE", f"/api/v1/workspaces/{WS}/tasks", "GET, POST"),
    ],
)
def test_unsupported_method_returns_405_with_allow_header(method, path, allow):
    r = call(method, path)
    assert r.status_code == 405
    assert r.headers["Allow"] == allow
    assert UUID.match(r.headers["X-Request-ID"])
    err = r.json()["error"]
    assert err["code"] == "METHOD_NOT_ALLOWED"
    assert err["request_id"] == r.headers["X-Request-ID"]
    assert err["details"] == []


def test_allowed_origin_gets_cors_headers_on_normal_responses():
    r = call("GET", "/api/v1/me")
    assert r.status_code == 200
    assert r.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Origin" in r.headers["Vary"]
    exposed = r.headers["Access-Control-Expose-Headers"]
    for name in ("ETag", "X-Request-ID", "Retry-After"):
        assert name in exposed


def test_unlisted_origin_gets_no_cors_headers_but_is_not_blocked():
    r = call("GET", "/api/v1/me", origin="https://evil.example")
    assert r.status_code == 200
    assert "Access-Control-Allow-Origin" not in r.headers
    assert "Access-Control-Expose-Headers" not in r.headers


def test_request_without_origin_gets_no_cors_headers():
    r = call("GET", "/api/v1/me", origin=None)
    assert r.status_code == 200
    assert "Access-Control-Allow-Origin" not in r.headers


def test_cors_headers_also_on_gateway_errors():
    r = call("DELETE", "/api/v1/me")
    assert r.status_code == 405
    assert r.headers["Access-Control-Allow-Origin"] == ORIGIN


@pytest.mark.parametrize(
    "path,methods",
    [
        ("/api/v1/me", "GET"),
        ("/api/v1/auth/google/exchange", "POST"),
        (f"/api/v1/workspaces/{WS}", "GET, PATCH, DELETE"),
        (f"/api/v1/workspaces/{WS}/tasks/t1/comments", "GET, POST"),
        ("/api/v1/notifications/n1/read", "POST"),
    ],
)
def test_preflight_from_allowed_origin_succeeds_without_credentials(path, methods):
    r = call("OPTIONS", path, headers={"Access-Control-Request-Method": methods.split(",")[0]})
    assert r.status_code == 204
    assert r.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert r.headers["Access-Control-Allow-Methods"] == methods
    allowed = r.headers["Access-Control-Allow-Headers"]
    for name in ("Authorization", "Content-Type", "Idempotency-Key", "If-Match", "X-Request-ID"):
        assert name in allowed
    assert "Origin" in r.headers["Vary"]
    assert r.text == ""


def test_preflight_from_denied_origin_gets_403_json():
    r = call("OPTIONS", "/api/v1/me", origin="https://evil.example")
    assert r.status_code == 403
    assert "Access-Control-Allow-Origin" not in r.headers
    err = r.json()["error"]
    assert err["code"] == "ORIGIN_NOT_ALLOWED"
    assert UUID.match(err["request_id"])
    assert err["details"] == []


def test_preflight_on_unknown_path_is_404():
    assert call("OPTIONS", "/api/v1/unknown").status_code == 404


@pytest.mark.parametrize("path", ["/api/v1/auth/google/challenges", "/api/v1/auth/google/exchange"])
@pytest.mark.parametrize("origin", [None, "https://evil.example"])
def test_login_endpoints_require_an_allowed_origin(path, origin):
    r = call("POST", path, origin=origin)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"


def test_services_own_cors_headers_are_not_duplicated():
    # The mock echoes request headers, not response ones, so assert the gateway
    # sends exactly one value even if it is asked twice (single header line).
    r = call("GET", "/api/v1/me")
    assert r.raw.headers.get_all("Access-Control-Allow-Origin") == [ORIGIN]
    assert len(r.raw.headers.get_all("X-Request-ID")) == 1


def test_body_over_64_kib_is_rejected_with_json_error():
    r = call("POST", "/api/v1/auth/google/exchange", data=b"x" * (64 * 1024 + 1))
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_body_at_limit_is_accepted():
    r = call("POST", "/api/v1/auth/google/exchange", data=b"x" * (64 * 1024))
    assert r.status_code == 200
    assert r.json()["body_bytes"] == 64 * 1024


def test_gateway_liveness_probe():
    r = call("GET", "/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_unreachable_upstream_returns_503_with_retry_after():
    # pause freezes the container, so Nginx waits for its 10 s timeout and
    # answers 504 internally, which the gateway maps to the contract's 503.
    compose("pause", "notification-mock")
    try:
        r = requests.get(BASE + "/api/v1/notifications", timeout=20)
    finally:
        compose("unpause", "notification-mock")
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "5"
    assert UUID.match(r.headers["X-Request-ID"])
    err = r.json()["error"]
    assert err["code"] == "DEPENDENCY_UNAVAILABLE"
    assert UUID.match(err["request_id"])
    assert err["details"] == []


# ---- Throttling -------------------------------------------------------------
# Rate-limit state is shared by every request from this client IP, so these tests
# restart the gateway (which resets the counters) before and after they run.


def restart_gateway():
    compose("restart", "gateway")
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if requests.get(BASE + "/healthz", timeout=2).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError("gateway did not come back after restart")


@pytest.fixture
def fresh_limits():
    restart_gateway()
    yield
    restart_gateway()


def test_login_endpoint_allows_10_per_minute_then_429(fresh_limits):
    path = "/api/v1/auth/google/challenges"
    # Forged X-Forwarded-For values must not give a client a fresh budget.
    statuses = [
        call("POST", path, headers={"X-Forwarded-For": f"10.9.8.{i}"}).status_code
        for i in range(10)
    ]
    assert statuses == [200] * 10

    r = call("POST", path, headers={"X-Forwarded-For": "10.9.8.200"})
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "6"
    assert r.headers["Access-Control-Allow-Origin"] == ORIGIN
    err = r.json()["error"]
    assert err["code"] == "RATE_LIMITED"
    assert UUID.match(err["request_id"])
    assert err["details"] == []

    # The other login endpoint has its own budget.
    assert call("POST", "/api/v1/auth/google/exchange").status_code == 200


def test_other_requests_are_limited_to_about_120_per_minute(fresh_limits):
    first_429 = None
    for i in range(1, 201):
        r = call("GET", "/api/v1/me", headers={"X-Forwarded-For": f"10.7.7.{i % 250}"})
        if r.status_code == 429:
            first_429 = i
            break
    # 120 allowed immediately; a few more may slip through while the loop runs
    # because the budget refills at 2 per second.
    assert first_429 is not None and 120 < first_429 <= 160
    assert r.headers["Retry-After"] == "1"
    assert r.json()["error"]["code"] == "RATE_LIMITED"

    # Gateway health probe and the login budget are unaffected.
    assert call("GET", "/healthz", origin=None).status_code == 200
    assert call("POST", "/api/v1/auth/google/challenges").status_code == 200