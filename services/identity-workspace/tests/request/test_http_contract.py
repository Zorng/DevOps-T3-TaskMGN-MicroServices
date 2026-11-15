import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import Annotated
from unittest.mock import Mock
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.testclient import TestClient
from pydantic import Field, field_validator
import pytest

from identity_workspace.correlation import request_id_context
from identity_workspace.errors import APIError
from identity_workspace.http_contract import MAX_BODY_BYTES
from identity_workspace.main import create_app
from identity_workspace.validation import ContractModel, WorkspaceName

ERROR_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[4] / 'api_contracts/openapi/identity-workspace.openapi.json').read_text(encoding='utf-8')
)["components"]["schemas"]["Error"]
ORIGIN = "http://localhost:5173"


class Input(ContractModel):
    name: WorkspaceName
    count: int
    enabled: bool
    description: str = Field(default="", max_length=65536)


@pytest.fixture
def app(settings):
    configured = settings.model_copy(update={"allowed_origins": (ORIGIN,)})
    database = Mock()
    database.check_readiness.return_value = True
    app = create_app(configured, database_factory=Mock(return_value=database))
    app.state.calls = []

    @app.post("/api/v1/_tests/echo")
    def echo(payload: Input, response: Response, limit: int = Query(default=20, ge=1, le=100)):
        app.state.calls.append(payload)
        response.headers["ETag"] = '"1"'
        return {**payload.model_dump(mode="json"), "limit": limit}

    @app.get("/api/v1/_tests/failure")
    def fail():
        raise RuntimeError("synthetic-secret-in-unexpected-error")

    @app.get("/api/v1/_tests/response-error", response_model=Input)
    def bad_response():
        return {"name": "valid", "count": "synthetic-secret", "enabled": True}

    @app.get("/api/v1/_tests/http-error/{status}")
    def http_error(status: int):
        raise HTTPException(status_code=status, detail="synthetic-secret-in-exception-detail")

    @app.get("/api/v1/_tests/dependency")
    def dependency_error():
        raise APIError(503, retry_after=3)

    @app.get("/api/v1/_tests/conflict")
    def conflict():
        raise APIError(409, code="WORKSPACE_LIMIT_REACHED", message="Workspace ownership limit reached.")

    @app.get("/api/v1/_tests/context")
    async def context(request: Request):
        await asyncio.sleep(0)
        return {"request_id": request_id_context.get(), "state_id": request.state.request_id}

    return app


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def payload(**overrides):
    values = {"name": "  Example  ", "count": 1, "enabled": True}
    values.update(overrides)
    return values


def assert_error(response, status, code):
    assert response.status_code == status
    data = response.json()
    assert set(data) == set(ERROR_SCHEMA["required"])
    error = data["error"]
    assert set(error) == set(ERROR_SCHEMA["properties"]["error"]["required"])
    assert error["code"] == code
    assert error["request_id"] == response.headers["x-request-id"]
    assert str(UUID(error["request_id"])) == error["request_id"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/json"
    assert isinstance(error["details"], list)
    for detail in error["details"]:
        assert set(detail) == {"field", "reason"}
        assert 1 <= len(detail["field"]) <= 150
        assert 1 <= len(detail["reason"]) <= 200
    assert "synthetic-secret" not in response.text
    return error


def test_valid_json_reaches_handler_with_normalized_fields_and_shared_headers(client):
    response = client.post("/api/v1/_tests/echo?limit=3", json=payload())
    assert response.status_code == 200
    assert response.json()["name"] == "Example"
    assert response.json()["limit"] == 3
    assert response.headers["etag"] == '"1"'
    assert response.headers["cache-control"] == "no-store"
    UUID(response.headers["x-request-id"])


@pytest.mark.parametrize("override", [{"count": "1"}, {"count": True}, {"enabled": "true"}, {"enabled": 1}, {"name": 1}, {"name": " \t "}, {"name": "😀" * 101}])
def test_invalid_json_fields_are_422_and_do_not_call_handler(client, app, override):
    error = assert_error(client.post("/api/v1/_tests/echo", json=payload(**override)), 422, "VALIDATION_FAILED")
    assert error["details"]
    assert app.state.calls == []


def test_unknown_json_properties_do_not_expose_their_names_or_values(client):
    error = assert_error(client.post("/api/v1/_tests/echo", json=payload(**{"synthetic-secret-key": "synthetic-secret-value"})), 422, "VALIDATION_FAILED")
    assert error["details"][0]["field"] == "body"


def test_missing_body_is_422(client):
    assert_error(client.post("/api/v1/_tests/echo"), 422, "VALIDATION_FAILED")


@pytest.mark.parametrize("body", [b"{", b"\xff", b'{"count":NaN}', b'{"count":Infinity}'])
def test_malformed_json_is_400(client, app, body):
    assert_error(client.post("/api/v1/_tests/echo", content=body, headers={"Content-Type": "application/json"}), 400, "MALFORMED_REQUEST")
    assert app.state.calls == []


@pytest.mark.parametrize("media_type", [None, "text/plain", "application/x-www-form-urlencoded", "application/json; charset=utf-16"])
def test_body_requires_utf8_json_media_type(client, media_type):
    headers = {"Content-Type": media_type} if media_type else {}
    assert_error(client.post("/api/v1/_tests/echo", content=json.dumps(payload()).encode(), headers=headers), 415, "UNSUPPORTED_MEDIA_TYPE")


def test_compressed_request_body_is_not_silently_accepted(client):
    assert_error(client.post("/api/v1/_tests/echo", json=payload(), headers={"Content-Encoding": "gzip"}), 415, "UNSUPPORTED_MEDIA_TYPE")


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
def test_health_operations_reject_bodies_and_unknown_query_parameters(client, path):
    assert_error(client.request("GET", path, content=b"{}", headers={"Content-Type": "application/json"}), 400, "MALFORMED_REQUEST")
    assert_error(client.get(path + "?synthetic-secret-key=synthetic-secret-value"), 400, "MALFORMED_REQUEST")


def test_bodyless_operation_does_not_require_or_validate_content_type(client):
    response = client.get("/health/live", headers={"Content-Type": "text/plain"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_query_is_400_and_bad_known_query_is_422(client):
    assert_error(client.post("/api/v1/_tests/echo?unknown=synthetic-secret", json=payload()), 400, "MALFORMED_REQUEST")
    error = assert_error(client.post("/api/v1/_tests/echo?limit=synthetic-secret", json=payload()), 422, "VALIDATION_FAILED")
    assert error["details"][0]["field"] == "limit"


def test_body_limit_is_exactly_65536_utf8_bytes(client, app):
    values = payload(description="")
    overhead = len(json.dumps(values, separators=(",", ":")).encode())
    values["description"] = "x" * (MAX_BODY_BYTES - overhead)
    body = json.dumps(values, separators=(",", ":")).encode()
    assert len(body) == MAX_BODY_BYTES
    assert client.post("/api/v1/_tests/echo", content=body, headers={"Content-Type": "application/json"}).status_code == 200
    app.state.calls.clear()
    assert_error(client.post("/api/v1/_tests/echo", content=body + b" ", headers={"Content-Type": "application/json"}), 413, "PAYLOAD_TOO_LARGE")
    assert app.state.calls == []


def test_body_limit_counts_bytes_rather_than_characters(client):
    body = json.dumps(payload(description="😀" * 16384), ensure_ascii=False).encode()
    assert len(body) > MAX_BODY_BYTES
    assert_error(client.post("/api/v1/_tests/echo", content=body, headers={"Content-Type": "application/json"}), 413, "PAYLOAD_TOO_LARGE")


def test_streamed_request_is_also_size_limited(client, app):
    chunks = (chunk for chunk in [b"a" * 32768, b"b" * 32769])
    assert_error(client.post("/api/v1/_tests/echo", content=chunks, headers={"Content-Type": "application/json"}), 413, "PAYLOAD_TOO_LARGE")
    assert app.state.calls == []


def test_unknown_routes_and_methods_use_contract_errors(client):
    assert_error(client.get("/does-not-exist"), 404, "NOT_FOUND")
    assert_error(client.get("/health/live/"), 404, "NOT_FOUND")
    response = client.post("/health/live")
    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert "GET" in response.headers["allow"]


@pytest.mark.parametrize("path", ["/api/v1/_tests/failure", "/api/v1/_tests/response-error", "/api/v1/_tests/http-error/500"])
def test_unexpected_and_response_validation_errors_are_safe_500(client, caplog, path):
    assert_error(client.get(path), 500, "INTERNAL_ERROR")
    assert "synthetic-secret" not in caplog.text


def test_http_exception_details_are_not_returned(client):
    assert_error(client.get("/api/v1/_tests/http-error/404"), 404, "NOT_FOUND")


def test_retry_after_and_specific_domain_error_codes(client):
    response = client.get("/api/v1/_tests/dependency")
    assert_error(response, 503, "DEPENDENCY_UNAVAILABLE")
    assert response.headers["retry-after"] == "3"
    assert_error(client.get("/api/v1/_tests/conflict"), 409, "WORKSPACE_LIMIT_REACHED")


def test_request_id_matches_across_error_headers_and_body(client):
    identifier = str(uuid4())
    response = client.get("/missing", headers={"X-Request-ID": identifier})
    assert_error(response, 404, "NOT_FOUND")
    assert response.headers["x-request-id"] == identifier


def test_concurrent_request_contexts_do_not_leak_into_each_other(client):
    identifiers = [str(uuid4()) for _ in range(8)]

    def fetch(identifier):
        response = client.get("/api/v1/_tests/context", headers={"X-Request-ID": identifier})
        assert response.json() == {"request_id": identifier, "state_id": identifier}
        assert response.headers["x-request-id"] == identifier

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(fetch, identifiers))


def test_allowed_origin_can_read_success_and_error_responses(client):
    for response in (
        client.post("/api/v1/_tests/echo", json=payload(), headers={"Origin": ORIGIN}),
        client.get("/api/v1/_tests/failure", headers={"Origin": ORIGIN}),
        client.get("/missing", headers={"Origin": ORIGIN}),
        client.post("/api/v1/_tests/echo", json=payload(count="1"), headers={"Origin": ORIGIN}),
    ):
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert "Origin" in response.headers["vary"]
        assert {"etag", "x-request-id", "retry-after"} <= {value.strip().lower() for value in response.headers["access-control-expose-headers"].split(",")}
        assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("origin", ["https://untrusted.example", "null", "http://localhost:5173.evil.example"])
def test_disallowed_origins_receive_safe_403_without_origin_echo(client, origin):
    response = client.get("/api/v1/_tests/context", headers={"Origin": origin})
    assert_error(response, 403, "ORIGIN_NOT_ALLOWED")
    assert "access-control-allow-origin" not in response.headers


def test_preflight_is_bodyless_and_does_not_execute_the_endpoint(client, app):
    response = client.options("/api/v1/_tests/echo", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization, Content-Type, Idempotency-Key, If-Match, X-Request-ID",
    })
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]
    assert response.headers["cache-control"] == "no-store"
    UUID(response.headers["x-request-id"])
    assert app.state.calls == []


@pytest.mark.parametrize("path,method,extra,status,code", [
    ("/api/v1/_tests/echo", "DELETE", {}, 405, "METHOD_NOT_ALLOWED"),
    ("/api/v1/_tests/missing", "POST", {}, 404, "NOT_FOUND"),
    ("/health/live", "GET", {}, 404, "NOT_FOUND"),
    ("/api/v1/_tests/echo", "POST", {"Access-Control-Request-Headers": "X-Service-Key"}, 400, "MALFORMED_REQUEST"),
])
def test_preflight_rejects_unsupported_targets_and_headers(client, path, method, extra, status, code):
    response = client.options(path, headers={"Origin": ORIGIN, "Access-Control-Request-Method": method, **extra})
    assert_error(response, status, code)


def test_nested_router_and_dependency_query_parameters_are_recognized(settings):
    def pagination(cursor: str | None = None):
        return cursor

    router = APIRouter(prefix="/nested", dependencies=[Depends(pagination)])

    @router.get("/items")
    def items(query: Annotated[str | None, Query(alias="q")] = None):
        return {"q": query}

    app = create_app(settings, database_factory=Mock(return_value=Mock()))
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as client:
        assert client.get("/api/v1/nested/items?q=example&cursor=opaque").status_code == 200
        assert_error(client.get("/api/v1/nested/items?query=not-allowed"), 400, "MALFORMED_REQUEST")


def test_custom_validator_error_cannot_disclose_submitted_text(settings, caplog):
    class SensitiveInput(ContractModel):
        value: str

        @field_validator("value")
        @classmethod
        def reject(cls, value):
            raise ValueError(f"Rejected upstream value: {value}")

    app = create_app(settings, database_factory=Mock(return_value=Mock()))

    @app.post("/api/v1/_tests/sensitive")
    def endpoint(body: SensitiveInput):
        return {}

    with TestClient(app) as client:
        error = assert_error(client.post("/api/v1/_tests/sensitive", json={"value": "synthetic-secret"}), 422, "VALIDATION_FAILED")
    assert error["details"][0]["field"] == "value"
    assert "synthetic-secret" not in caplog.text


def test_preflight_does_not_require_an_application_session(settings):
    authenticate = Mock()

    def require_session(authorization: Annotated[str | None, Header()] = None):
        authenticate()
        raise HTTPException(401, detail="synthetic-secret-auth-diagnostic")

    app = create_app(settings.model_copy(update={"allowed_origins": (ORIGIN,)}), database_factory=Mock(return_value=Mock()))
    router = APIRouter(dependencies=[Depends(require_session)])

    @router.get("/protected")
    def protected():
        return {}

    app.include_router(router, prefix="/api/v1/_tests")
    with TestClient(app) as client:
        response = client.options("/api/v1/_tests/protected", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"})
        assert response.status_code == 204
        authenticate.assert_not_called()
        response = client.get("/api/v1/_tests/protected", headers={"Origin": ORIGIN})
        assert_error(response, 401, "AUTH_REQUIRED")
        assert response.headers["www-authenticate"] == "Bearer"


def test_cors_response_headers_are_owned_by_the_shared_layer(settings):
    app = create_app(settings.model_copy(update={"allowed_origins": (ORIGIN,)}), database_factory=Mock(return_value=Mock()))

    @app.get("/api/v1/_tests/headers")
    def headers(response: Response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        return {}

    with TestClient(app) as client:
        without_origin = client.get("/api/v1/_tests/headers")
        assert "access-control-allow-origin" not in without_origin.headers
        allowed = client.get("/api/v1/_tests/headers", headers={"Origin": ORIGIN})
        assert allowed.headers["access-control-allow-origin"] == ORIGIN
        assert "access-control-allow-credentials" not in allowed.headers


def test_requests_with_origin_are_denied_when_no_origins_are_configured(settings):
    app = create_app(settings, database_factory=Mock(return_value=Mock()))
    with TestClient(app) as client:
        assert_error(client.get("/health/live", headers={"Origin": ORIGIN}), 403, "ORIGIN_NOT_ALLOWED")


def test_preflight_requires_an_origin(client):
    assert_error(client.options("/api/v1/_tests/echo", headers={"Access-Control-Request-Method": "POST"}), 403, "ORIGIN_NOT_ALLOWED")
