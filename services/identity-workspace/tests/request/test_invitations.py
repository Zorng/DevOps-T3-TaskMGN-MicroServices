from io import StringIO
from unittest.mock import Mock
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.errors import APIError
from identity_workspace.main import create_app
from tests.workspace_contract import PUBLIC, public_contract

OPERATIONS = ["create", "list_owned", "list_received", "read", "respond", "cancel"]
BEARER = {"Authorization": "Bearer " + "S" * 43}


@pytest.fixture
def invitation_client(invitation_dependencies):
    d = invitation_dependencies
    stream = StringIO()
    app = create_app(d.settings, database_factory=Mock(), google_verifier_factory=Mock(),
                     invitation_store_factory=Mock(return_value=d.store), log_stream=stream)
    with TestClient(app) as client:
        yield client, d, stream


def path(d, operation, *, template=False):
    workspace = "{workspace_id}" if template else str(d.workspace_id)
    invitation = "{invitation_id}" if template else str(d.identifier)
    return {
        "create": f"/api/v1/workspaces/{workspace}/invitations",
        "list_owned": f"/api/v1/workspaces/{workspace}/invitations",
        "list_received": "/api/v1/invitations", "read": f"/api/v1/invitations/{invitation}",
        "respond": f"/api/v1/invitations/{invitation}/response", "cancel": f"/api/v1/invitations/{invitation}/cancel",
    }[operation]


def send(client, d, operation, *, headers=None, url=None, **options):
    method = "POST" if operation in {"create", "respond", "cancel"} else "GET"
    body = {"json": {"email": "recipient@example.com"}} if operation == "create" else (
        {"json": {"action": "accept"}} if operation == "respond" else {})
    body.update(options)
    return client.request(method, url or path(d, operation),
                          headers={**BEARER, "Idempotency-Key": str(d.key)} if headers is None else headers, **body)


def shared(response, d, operation, status):
    assert response.status_code == status
    method = "post" if operation in {"create", "respond", "cancel"} else "get"
    assert str(status) in PUBLIC["paths"][path(d, operation, template=True)][method]["responses"]
    assert response.headers["cache-control"] == "no-store"
    assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
    if status >= 400:
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_each_endpoint_success_matches_contract_and_has_no_etag_or_email(invitation_client, operation):
    client, d, stream = invitation_client
    response = send(client, d, operation)
    shared(response, d, operation, 201 if operation == "create" else 200)
    public_contract(response.json(), "InvitationPage" if operation.startswith("list_") else "Invitation")
    assert "etag" not in response.headers and "email" not in response.text
    assert "recipient@example.com" not in stream.getvalue() and "S" * 43 not in stream.getvalue()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("headers,code", [({}, "AUTH_REQUIRED"), ({"Authorization": "Bearer bad"}, "SESSION_INVALID")])
def test_every_invitation_endpoint_requires_a_valid_bearer(invitation_client, operation, headers, code):
    client, d, _ = invitation_client
    response = send(client, d, operation, headers=headers)
    shared(response, d, operation, 401)
    assert response.json()["error"]["code"] == code
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("status,code", [(401, "SESSION_INVALID"), (404, "NOT_FOUND")])
def test_each_endpoint_preserves_live_session_and_visibility_errors(invitation_client, operation, status, code):
    client, d, _ = invitation_client
    getattr(d.store, operation).side_effect = APIError(status, code=code)
    shared(send(client, d, operation), d, operation, status)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_each_endpoint_sanitizes_storage_failures(invitation_client, operation):
    client, d, stream = invitation_client
    getattr(d.store, operation).side_effect = SQLAlchemyError("synthetic-sensitive-storage-error")
    response = send(client, d, operation)
    shared(response, d, operation, 503)
    assert "synthetic-sensitive" not in response.text + stream.getvalue()


@pytest.mark.parametrize("operation", ["create", "list_owned", "respond", "cancel"])
def test_known_visible_but_forbidden_invitation_actions_return_403(invitation_client, operation):
    client, d, _ = invitation_client
    getattr(d.store, operation).side_effect = APIError(403)
    shared(send(client, d, operation), d, operation, 403)


@pytest.mark.parametrize("body", [{}, {"email": ""}, {"email": None}, {"email": 1}, {"email": "invalid"}, {"email": "x" * 255}, {"email": "recipient@example.com", "private-field": "private-input"}])
def test_create_strict_email_validation_is_secret_safe(invitation_client, body):
    client, d, stream = invitation_client
    response = send(client, d, "create", json=body)
    shared(response, d, "create", 422)
    assert "private-field" not in response.text and "private-input" not in response.text + stream.getvalue()
    d.store.create.assert_not_called()


def test_create_trims_and_lowercases_without_changing_aliases(invitation_client):
    client, d, _ = invitation_client
    shared(send(client, d, "create", json={"email": " \tPerson.Name+tag@EXAMPLE.COM\n "}), d, "create", 201)
    assert d.store.create.call_args.args[2] == "person.name+tag@example.com"


@pytest.mark.parametrize("headers,code", [(BEARER, "IDEMPOTENCY_KEY_REQUIRED"), ({**BEARER, "Idempotency-Key": "bad"}, "MALFORMED_REQUEST")])
def test_create_requires_uuid_key(invitation_client, headers, code):
    client, d, _ = invitation_client
    response = send(client, d, "create", headers=headers)
    shared(response, d, "create", 400)
    assert response.json()["error"]["code"] == code
    d.store.create.assert_not_called()


@pytest.mark.parametrize("status,code", [(404, "USER_NOT_FOUND"), (409, "EMAIL_AMBIGUOUS"), (409, "ALREADY_MEMBER"), (409, "INVITATION_ALREADY_PENDING"), (409, "IDEMPOTENCY_CONFLICT"), (409, "IDEMPOTENCY_IN_PROGRESS")])
def test_create_contract_lookup_and_idempotency_errors(invitation_client, status, code):
    client, d, _ = invitation_client
    d.store.create.side_effect = APIError(status, code=code)
    response = send(client, d, "create")
    shared(response, d, "create", status)
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("body", [{}, {"action": None}, {"action": "cancel"}, {"action": "ACCEPT"}, {"action": True}, {"action": "accept", "private-field": "private-input"}])
def test_response_action_validation(invitation_client, body):
    client, d, _ = invitation_client
    shared(send(client, d, "respond", json=body), d, "respond", 422)
    d.store.respond.assert_not_called()


@pytest.mark.parametrize("operation,code", [("respond", "INVITATION_NOT_PENDING"), ("respond", "MEMBER_LIMIT_REACHED"), ("cancel", "INVITATION_NOT_PENDING")])
def test_terminal_and_capacity_conflicts(invitation_client, operation, code):
    client, d, _ = invitation_client
    getattr(d.store, operation).side_effect = APIError(409, code=code)
    shared(send(client, d, operation), d, operation, 409)


@pytest.mark.parametrize("operation", ["list_owned", "list_received", "read", "cancel"])
def test_bodyless_invitation_operations_reject_bodies(invitation_client, operation):
    client, d, _ = invitation_client
    shared(send(client, d, operation, json={}), d, operation, 400)
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS)
def test_no_email_lookup_query_or_directory_is_exposed(invitation_client, operation):
    client, d, stream = invitation_client
    response = send(client, d, operation, url=path(d, operation) + "?email=private@example.com")
    shared(response, d, operation, 400)
    assert "private@example.com" not in response.text + stream.getvalue()
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", ["list_owned", "list_received"])
@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=1.0", "status=expired", "status=", "limit=1&limit=2", "cursor=x&cursor=y", "status=pending&status=accepted"])
def test_list_parameters_are_bounded_and_unambiguous(invitation_client, operation, query):
    client, d, _ = invitation_client
    shared(send(client, d, operation, url=path(d, operation) + "?" + query), d, operation, 400)
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", ["list_owned", "list_received"])
def test_lists_pass_filter_limit_and_cursor(invitation_client, operation):
    client, d, _ = invitation_client
    shared(send(client, d, operation, params={"limit": "5", "status": "pending", "cursor": "C" * 43}), d, operation, 200)
    assert getattr(d.store, operation).call_args.args[-3:] == (5, "C" * 43, "pending")


@pytest.mark.parametrize("operation", ["create", "list_owned", "read", "respond", "cancel"])
def test_invitation_path_identifiers_are_validated(invitation_client, operation):
    client, d, _ = invitation_client
    url = path(d, operation).replace(str(d.workspace_id), "invalid-uuid").replace(str(d.identifier), "invalid-uuid")
    shared(send(client, d, operation, url=url), d, operation, 400)
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", ["create", "respond"])
def test_oversized_json_body_is_rejected_before_persistence(invitation_client, operation):
    client, d, _ = invitation_client
    options = {"json": None, "content": b'{"value":"' + b'x' * 65536 + b'"}'}
    response = send(client, d, operation, headers={**BEARER, "Content-Type": "application/json", "Idempotency-Key": str(d.key)}, **options)
    shared(response, d, operation, 413)
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS)
def test_invitation_routes_support_cors_preflight(invitation_client, operation):
    client, d, _ = invitation_client
    response = client.options(path(d, operation), headers={
        "Origin": d.settings.allowed_origins[0], "Access-Control-Request-Method": "POST" if operation in {"create", "respond", "cancel"} else "GET",
        "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
    })
    assert response.status_code == 204
