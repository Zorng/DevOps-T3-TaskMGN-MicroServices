"""Request-level tests: send real HTTP requests to a running gateway."""
import os
import re
import subprocess

import pytest
import requests

BASE = os.environ.get("GATEWAY_URL", "http://localhost:8080")
COMPOSE_FILE = os.path.join(os.path.dirname(__file__), "..", "compose.test.yaml")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
WS = "11111111-1111-4111-8111-111111111111"


def call(method, path, **kwargs):
    return requests.request(method, BASE + path, timeout=10, **kwargs)


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