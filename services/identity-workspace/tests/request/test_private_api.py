from io import StringIO
from unittest.mock import Mock
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.errors import APIError
from identity_workspace.main import create_app
from tests.workspace_contract import PRIVATE, private_contract


@pytest.fixture
def private_client(private_dependencies):
    d = private_dependencies
    stream = StringIO()
    app = create_app(d.settings, database_factory=Mock(), google_verifier_factory=Mock(),
                     private_store_factory=Mock(return_value=d.store), log_stream=stream)
    with TestClient(app) as client:
        yield client, d, stream


def path(d, operation):
    return "/internal/v1/auth/introspect" if operation == "introspect" else f"/internal/v1/workspaces/{d.workspace_id}/access"


def headers(d, caller="task"):
    return {"Authorization": "Bearer " + "S" * 43, "X-Service-Key": getattr(d, caller + "_key")}


def shared(response, operation, status):
    assert response.status_code == status
    template = "/internal/v1/auth/introspect" if operation == "introspect" else "/internal/v1/workspaces/{workspace_id}/access"
    assert str(status) in PRIVATE["paths"][template]["post"]["responses"]
    assert response.headers["cache-control"] == "no-store"
    assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
    assert "access-control-allow-origin" not in response.headers
    if status >= 400:
        private_contract(response.json(), "Error")
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize("operation,caller,status", [("introspect", "task", 200), ("introspect", "notification", 200),
                                                      ("workspace_access", "task", 200), ("workspace_access", "notification", 403)])
def test_exact_caller_matrix_and_fixture_responses(private_client, operation, caller, status):
    client, d, stream = private_client
    response = client.post(path(d, operation), headers=headers(d, caller))
    shared(response, operation, status)
    if status == 200:
        assert response.json() == getattr(d.store, operation).return_value
        private_contract(response.json(), "SessionIdentity" if operation == "introspect" else "AccessContext")
        assert "email" not in response.text and "provider_subject" not in response.text
    else:
        d.store.workspace_access.assert_not_called()
    assert all(value not in stream.getvalue() for value in (d.task_key, d.notification_key, "S" * 43))


@pytest.mark.parametrize("operation", ["introspect", "workspace_access"])
@pytest.mark.parametrize("kind,code", [("missing_key", "SERVICE_CREDENTIAL_INVALID"), ("bad_key", "SERVICE_CREDENTIAL_INVALID"),
                                      ("missing_bearer", "AUTH_REQUIRED"), ("bad_bearer", "SESSION_INVALID"),
                                      ("google_token", "SESSION_INVALID"), ("duplicate_key", "SERVICE_CREDENTIAL_INVALID")])
def test_both_credentials_are_mandatory_and_malformed_duplicates_fail(private_client, operation, kind, code):
    client, d, _ = private_client
    request_headers = headers(d)
    if kind == "missing_key": request_headers.pop("X-Service-Key")
    if kind == "bad_key": request_headers["X-Service-Key"] = "X" * 43
    if kind == "missing_bearer": request_headers.pop("Authorization")
    if kind == "bad_bearer": request_headers["Authorization"] = "Bearer bad"
    if kind == "google_token": request_headers["Authorization"] = "Bearer synthetic.google.token"
    if kind == "duplicate_key": request_headers = [*request_headers.items(), ("X-Service-Key", d.task_key)]
    response = client.post(path(d, operation), headers=request_headers)
    shared(response, operation, 401)
    assert response.json()["error"]["code"] == code
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", ["introspect", "workspace_access"])
@pytest.mark.parametrize("code,status", [("SESSION_INVALID", 401), ("NOT_FOUND", 404)])
def test_revocation_and_inaccessibility_errors_remain_contract_errors(private_client, operation, code, status):
    client, d, _ = private_client
    getattr(d.store, operation).side_effect = APIError(status, code=code)
    response = client.post(path(d, operation), headers=headers(d))
    shared(response, operation, status)
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("operation", ["introspect", "workspace_access"])
def test_dependency_failure_cannot_return_a_cached_success(private_client, operation):
    client, d, stream = private_client
    assert client.post(path(d, operation), headers=headers(d)).status_code == 200
    getattr(d.store, operation).side_effect = SQLAlchemyError("synthetic-private-database-credential")
    response = client.post(path(d, operation), headers=headers(d))
    shared(response, operation, 503)
    assert "synthetic-private" not in response.text + stream.getvalue()
    assert getattr(d.store, operation).call_count == 2


@pytest.mark.parametrize("operation", ["introspect", "workspace_access"])
def test_identity_assertion_headers_cannot_replace_original_bearer(private_client, operation):
    client, d, _ = private_client
    forged = {"X-Service-Key": d.task_key, "X-User-ID": d.store.introspect.return_value["user"]["id"],
              "X-User-Role": "owner", "X-Forwarded-User": "owner", "X-Service-Name": "task"}
    shared(client.post(path(d, operation), headers=forged), operation, 401)
    getattr(d.store, operation).assert_not_called()


def test_notification_cannot_claim_task_identity_in_a_header(private_client):
    client, d, _ = private_client
    response = client.post(path(d, "workspace_access"), headers={**headers(d, "notification"), "X-Service-Name": "task", "X-User-Role": "owner"})
    shared(response, "workspace_access", 403)
    d.store.workspace_access.assert_not_called()


@pytest.mark.parametrize("operation", ["introspect", "workspace_access"])
def test_internal_checks_are_bodyless_and_reject_queries(private_client, operation):
    client, d, _ = private_client
    shared(client.post(path(d, operation), headers=headers(d), json={"user_id": "private-input"}), operation, 400)
    shared(client.post(path(d, operation) + "?token=private-input", headers=headers(d)), operation, 400)
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", ["introspect", "workspace_access"])
@pytest.mark.parametrize("method", ["POST", "OPTIONS"])
def test_internal_routes_never_grant_browser_cors(private_client, operation, method):
    client, d, _ = private_client
    response = client.request(method, path(d, operation), headers={**headers(d),
                              "Origin": d.settings.allowed_origins[0], "Access-Control-Request-Method": "POST"})
    shared(response, operation, 403)
    assert response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    getattr(d.store, operation).assert_not_called()


def test_invalid_workspace_identifier_is_rejected(private_client):
    client, d, _ = private_client
    response = client.post("/internal/v1/workspaces/invalid/access", headers=headers(d))
    shared(response, "workspace_access", 422)
    d.store.workspace_access.assert_not_called()
