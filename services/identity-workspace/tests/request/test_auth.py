from io import StringIO
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.auth.google import GoogleTokenInvalid, GoogleVerificationUnavailable
from identity_workspace.auth.service import token_hash
from identity_workspace.errors import APIError
from identity_workspace.main import create_app

CONTRACT = json.loads((Path(__file__).resolve().parents[4] / "api_contracts/openapi/identity-workspace.openapi.json").read_text())
CHALLENGE = "/api/v1/auth/google/challenges"
EXCHANGE = "/api/v1/auth/google/exchange"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/me"
BEARER = {"Authorization": "Bearer " + "S" * 43}


@pytest.fixture
def auth_client(auth_dependencies):
    d = auth_dependencies
    stream = StringIO()
    settings = d.settings.model_copy(update={"log_level": "DEBUG"})
    app = create_app(
        settings, database_factory=Mock(), google_verifier_factory=Mock(return_value=d.verifier),
        auth_store_factory=Mock(return_value=d.store), log_stream=stream,
    )
    with TestClient(app) as client:
        yield client, d, stream


def headers(d):
    return {"Origin": d.settings.allowed_origins[0]}


def body(d):
    return {"challenge_id": str(d.identifier), "id_token": "synthetic-sensitive-google-token"}


def shared(response, path, method, status):
    assert response.status_code == status
    assert str(status) in CONTRACT["paths"][path][method]["responses"]
    assert response.headers["cache-control"] == "no-store"
    assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
    if status >= 400:
        assert set(response.json()) == {"error"}
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_create_challenge_matches_contract_and_cors_without_authentication(auth_client):
    client, d, stream = auth_client
    response = client.post(CHALLENGE, headers=headers(d))
    shared(response, CHALLENGE, "post", 201)
    data = response.json()
    assert set(data) == {"challenge_id", "nonce", "expires_at"}
    assert len(data["nonce"]) == 43 and data["expires_at"].endswith("Z")
    assert response.headers["access-control-allow-origin"] == d.settings.allowed_origins[0]
    assert d.store.create_challenge.call_args.args[0] == token_hash(data["nonce"])
    assert data["nonce"] not in stream.getvalue()


def test_exchange_returns_session_and_public_profile_without_subject(auth_client):
    client, d, stream = auth_client
    response = client.post(EXCHANGE, json=body(d), headers=headers(d))
    shared(response, EXCHANGE, "post", 200)
    data = response.json()
    assert set(data) == {"access_token", "token_type", "expires_at", "user"}
    assert data["token_type"] == "Bearer" and len(data["access_token"]) == 43
    assert data["expires_at"].endswith("Z")
    assert set(data["user"]) == {"id", "display_name", "email", "created_at"}
    assert d.claims["sub"] not in response.text
    assert data["access_token"] not in stream.getvalue()
    assert body(d)["id_token"] not in stream.getvalue()


def test_current_user_returns_contract_profile_and_stored_owned_count(auth_client):
    client, d, _ = auth_client
    d.store.current_user.return_value = (d.user, 2)
    response = client.get(ME, headers=BEARER)
    shared(response, ME, "get", 200)
    assert response.json() == {"user": d.user.model_dump(), "owned_workspace_count": 2}


def test_logout_is_bodyless_and_only_passes_presented_session_hash(auth_client):
    client, d, _ = auth_client
    response = client.post(LOGOUT, headers=BEARER)
    shared(response, LOGOUT, "post", 204)
    assert response.content == b"" and "set-cookie" not in response.headers
    d.store.logout.assert_called_once_with(token_hash("S" * 43))


@pytest.mark.parametrize("path", [CHALLENGE, EXCHANGE])
@pytest.mark.parametrize("origin", [None, "https://denied.example.com"])
def test_both_login_endpoints_require_an_allowed_origin(auth_client, path, origin):
    client, d, _ = auth_client
    response = client.post(path, headers={"Origin": origin} if origin else {}, **({"json": body(d)} if path == EXCHANGE else {}))
    shared(response, path, "post", 403)
    assert response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    d.store.create_challenge.assert_not_called()
    d.store.exchange.assert_not_called()
    d.verifier.verify.assert_not_called()


@pytest.mark.parametrize("path", [CHALLENGE, EXCHANGE])
def test_duplicate_origins_are_rejected(auth_client, path):
    client, d, _ = auth_client
    response = client.post(path, headers=[("Origin", d.settings.allowed_origins[0])] * 2, **({"json": body(d)} if path == EXCHANGE else {}))
    shared(response, path, "post", 400)
    assert response.json()["error"]["code"] == "MALFORMED_REQUEST"
    d.store.exchange.assert_not_called()


@pytest.mark.parametrize("override", [
    {"challenge_id": "invalid"}, {"challenge_id": None}, {"id_token": ""},
    {"id_token": 123}, {"id_token": "x" * 16385}, {"synthetic-secret-property": "sensitive-value"},
])
def test_exchange_rejects_malformed_fields_before_provider(auth_client, override):
    client, d, _ = auth_client
    response = client.post(EXCHANGE, headers=headers(d), json={**body(d), **override})
    shared(response, EXCHANGE, "post", 422)
    assert "sensitive-value" not in response.text and "synthetic-secret-property" not in response.text
    d.verifier.verify.assert_not_called()
    d.store.exchange.assert_not_called()


@pytest.mark.parametrize("content,media,status", [(b'{"invalid"', "application/json", 400), (b"{}", "text/plain", 415)])
def test_exchange_rejects_invalid_json_or_media_type(auth_client, content, media, status):
    client, d, _ = auth_client
    response = client.post(EXCHANGE, headers={**headers(d), "Content-Type": media}, content=content)
    shared(response, EXCHANGE, "post", status)
    d.verifier.verify.assert_not_called()


@pytest.mark.parametrize("path,method", [(CHALLENGE, "post"), (LOGOUT, "post"), (ME, "get")])
def test_bodyless_auth_operations_reject_bodies(auth_client, path, method):
    client, d, _ = auth_client
    response = client.request(method, path, headers={**headers(d), **BEARER}, json={})
    shared(response, path, method, 400)


@pytest.mark.parametrize("path,method", [(CHALLENGE, "post"), (EXCHANGE, "post"), (LOGOUT, "post"), (ME, "get")])
def test_auth_operations_reject_unsupported_queries(auth_client, path, method):
    client, d, _ = auth_client
    response = client.request(method, path + "?unsupported=1", headers={**headers(d), **BEARER}, **({"json": body(d)} if path == EXCHANGE else {}))
    shared(response, path, method, 400)


@pytest.mark.parametrize("path,method", [(LOGOUT, "post"), (ME, "get")])
@pytest.mark.parametrize("authorization", [None, "Basic synthetic", "Bearer synthetic.invalid.jwt", "Bearer " + "S" * 42])
def test_protected_endpoints_require_an_opaque_bearer_session(auth_client, path, method, authorization):
    client, d, _ = auth_client
    response = client.request(method, path, headers={"Authorization": authorization} if authorization else {})
    shared(response, path, method, 401)
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == ("AUTH_REQUIRED" if authorization is None else "SESSION_INVALID")
    d.store.current_user.assert_not_called()
    d.store.logout.assert_not_called()


@pytest.mark.parametrize("path,method", [(LOGOUT, "post"), (ME, "get")])
def test_expired_revoked_or_unknown_sessions_are_401(auth_client, path, method):
    client, d, _ = auth_client
    d.store.logout.side_effect = d.store.current_user.side_effect = APIError(401, code="SESSION_INVALID")
    response = client.request(method, path, headers=BEARER)
    shared(response, path, method, 401)
    assert response.json()["error"]["code"] == "SESSION_INVALID"


def test_repeated_logout_returns_401(auth_client):
    client, d, _ = auth_client
    d.store.logout.side_effect = [None, APIError(401, code="SESSION_INVALID")]
    assert client.post(LOGOUT, headers=BEARER).status_code == 204
    assert client.post(LOGOUT, headers=BEARER).status_code == 401


def test_unknown_expired_or_consumed_challenge_is_401(auth_client):
    client, d, _ = auth_client
    d.store.challenge.return_value = None
    response = client.post(EXCHANGE, headers=headers(d), json=body(d))
    shared(response, EXCHANGE, "post", 401)
    assert response.json()["error"]["code"] == "AUTH_CHALLENGE_INVALID"
    d.verifier.verify.assert_not_called()


@pytest.mark.parametrize("damage", [{"nonce": "wrong"}, {"email_verified": False}, {"aud": "wrong"}, {"sub": ""}, {"exp": 1}, {"nbf": 253402300799}])
def test_invalid_google_claims_create_no_session(auth_client, damage):
    client, d, _ = auth_client
    d.verifier.verify.return_value = {**d.claims, **damage}
    response = client.post(EXCHANGE, headers=headers(d), json=body(d))
    shared(response, EXCHANGE, "post", 401)
    assert response.json()["error"]["code"] == "GOOGLE_TOKEN_INVALID"
    d.store.exchange.assert_not_called()


@pytest.mark.parametrize("failure,status", [(GoogleTokenInvalid(), 401), (GoogleVerificationUnavailable(), 503), (RuntimeError("synthetic-sensitive-provider-detail"), 503)])
def test_provider_failure_returns_safe_error_and_no_session(auth_client, failure, status):
    client, d, stream = auth_client
    d.verifier.verify.side_effect = failure
    response = client.post(EXCHANGE, headers=headers(d), json=body(d))
    shared(response, EXCHANGE, "post", status)
    if status == 503:
        assert int(response.headers["retry-after"]) > 0
    assert "synthetic-sensitive" not in response.text + stream.getvalue()
    d.store.exchange.assert_not_called()


@pytest.mark.parametrize("path,method", [(CHALLENGE, "post"), (EXCHANGE, "post"), (LOGOUT, "post"), (ME, "get")])
def test_dependency_failure_is_503_for_every_auth_endpoint(auth_client, path, method):
    client, d, stream = auth_client
    for operation in (d.store.rate_limit, d.store.logout, d.store.current_user):
        operation.side_effect = SQLAlchemyError("synthetic-sensitive-database-detail")
    response = client.request(method, path, headers={**headers(d), **BEARER}, **({"json": body(d)} if path == EXCHANGE else {}))
    shared(response, path, method, 503)
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "synthetic-sensitive" not in response.text + stream.getvalue()
    d.store.exchange.assert_not_called()


@pytest.mark.parametrize("path", [CHALLENGE, EXCHANGE])
def test_login_rate_limit_returns_retry_after_and_ignores_forged_forwarding(auth_client, path):
    client, d, _ = auth_client
    d.store.rate_limit.side_effect = APIError(429, retry_after=17)
    response = client.post(path, headers={**headers(d), "X-Forwarded-For": "203.0.113.99"}, **({"json": body(d)} if path == EXCHANGE else {}))
    shared(response, path, "post", 429)
    assert response.headers["retry-after"] == "17"
    assert d.store.rate_limit.call_args.args[1] != "203.0.113.99"
    d.store.exchange.assert_not_called()


def test_login_preflight_does_not_need_session_or_call_google(auth_client):
    client, d, _ = auth_client
    response = client.options(EXCHANGE, headers={**headers(d), "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type,X-Request-ID"})
    assert response.status_code == 204 and response.content == b""
    d.verifier.verify.assert_not_called()
    d.store.rate_limit.assert_not_called()


def test_no_refresh_endpoint_or_application_cookie_is_introduced(auth_client):
    client, d, _ = auth_client
    response = client.post(EXCHANGE, headers=headers(d), json=body(d))
    assert "set-cookie" not in response.headers
    assert client.post("/api/v1/auth/refresh").status_code == 404


@pytest.mark.parametrize("path", [CHALLENGE, EXCHANGE])
def test_unconfigured_google_returns_503_without_creating_authentication_state(auth_dependencies, path):
    d = auth_dependencies
    app = create_app(
        d.settings.model_copy(update={"google_client_id": None}), database_factory=Mock(),
        google_verifier_factory=Mock(return_value=d.verifier), auth_store_factory=Mock(return_value=d.store),
    )
    with TestClient(app) as client:
        response = client.post(path, headers=headers(d), **({"json": body(d)} if path == EXCHANGE else {}))
    shared(response, path, "post", 503)
    d.store.create_challenge.assert_not_called()
    d.store.exchange.assert_not_called()
    d.verifier.verify.assert_not_called()
