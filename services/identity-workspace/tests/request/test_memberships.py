from io import StringIO
from unittest.mock import Mock
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.errors import APIError
from identity_workspace.main import create_app
from tests.workspace_contract import PUBLIC, public_contract

BEARER = {"Authorization": "Bearer " + "S" * 43}
OPERATIONS = ["list", "remove", "leave"]


@pytest.fixture
def membership_client(membership_dependencies):
    d = membership_dependencies
    stream = StringIO()
    app = create_app(d.settings, database_factory=Mock(), google_verifier_factory=Mock(),
                     membership_store_factory=Mock(return_value=d.store), log_stream=stream)
    with TestClient(app) as client:
        yield client, d, stream


def path(d, operation, *, template=False):
    root = "/api/v1/workspaces/" + ("{workspace_id}" if template else str(d.workspace_id))
    return root + {
        "list": "/members", "remove": "/members/" + ("{user_id}" if template else str(d.user_id)),
        "leave": "/membership",
    }[operation]


def send(client, d, operation, *, headers=None, url=None, **options):
    return client.request("GET" if operation == "list" else "DELETE", url or path(d, operation),
                          headers=BEARER if headers is None else headers, **options)


def shared(response, d, operation, status):
    assert response.status_code == status
    method = "get" if operation == "list" else "delete"
    if status != 422:  # Shared path UUID validation also applies to GET.
        assert str(status) in PUBLIC["paths"][path(d, operation, template=True)][method]["responses"]
    assert response.headers["cache-control"] == "no-store"
    assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
    if status >= 400:
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_each_endpoint_success_matches_contract_and_safe_headers(membership_client, operation):
    client, d, stream = membership_client
    response = send(client, d, operation)
    shared(response, d, operation, 200 if operation == "list" else 204)
    if operation == "list":
        public_contract(response.json(), "MemberList")
        assert "email" not in response.text
    else:
        assert response.content == b"" and "etag" not in response.headers
    assert "S" * 43 not in stream.getvalue()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("headers,code", [({}, "AUTH_REQUIRED"), ({"Authorization": "Bearer bad"}, "SESSION_INVALID"), ([("Authorization", "Bearer " + "S" * 43)] * 2, "SESSION_INVALID")])
def test_each_endpoint_requires_well_formed_single_bearer(membership_client, operation, headers, code):
    client, d, _ = membership_client
    response = send(client, d, operation, headers=headers)
    shared(response, d, operation, 401)
    assert response.json()["error"]["code"] == code
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("status,code", [(401, "SESSION_INVALID"), (404, "NOT_FOUND")])
def test_each_endpoint_rejects_invalid_session_or_inaccessible_workspace(membership_client, operation, status, code):
    client, d, _ = membership_client
    getattr(d.store, operation).side_effect = APIError(status, code=code)
    response = send(client, d, operation)
    shared(response, d, operation, status)
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("operation", OPERATIONS)
def test_each_endpoint_hides_database_failure_details(membership_client, operation):
    client, d, stream = membership_client
    getattr(d.store, operation).side_effect = SQLAlchemyError("synthetic-sensitive-database-password")
    response = send(client, d, operation)
    shared(response, d, operation, 503)
    assert response.headers["retry-after"]
    assert "synthetic-sensitive" not in response.text + stream.getvalue()


@pytest.mark.parametrize("operation", ["remove", "leave"])
def test_owner_cannot_leave_or_be_removed(membership_client, operation):
    client, d, _ = membership_client
    getattr(d.store, operation).side_effect = APIError(409, code="OWNER_CANNOT_LEAVE")
    response = send(client, d, operation)
    shared(response, d, operation, 409)
    assert response.json()["error"]["code"] == "OWNER_CANNOT_LEAVE"


def test_nonowner_cannot_remove_anyone(membership_client):
    client, d, _ = membership_client
    d.store.remove.side_effect = APIError(403)
    shared(send(client, d, "remove"), d, "remove", 403)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_bodyless_member_operations_reject_json_body(membership_client, operation):
    client, d, _ = membership_client
    shared(send(client, d, operation, json={}), d, operation, 400)
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS)
def test_unknown_queries_are_rejected_without_echoing_search_input(membership_client, operation):
    client, d, stream = membership_client
    response = send(client, d, operation, url=path(d, operation) + "?email=private@example.com")
    shared(response, d, operation, 400)
    assert "private@example.com" not in response.text + stream.getvalue()
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("query", ["", "AlIcE", "%_/", "\U0001f600" * 100])
def test_search_is_passed_as_literal_bounded_text(membership_client, query):
    client, d, stream = membership_client
    response = send(client, d, "list", params={"q": query})
    shared(response, d, "list", 200)
    assert d.store.list.call_args.args[1:] == (d.workspace_id, query)
    if query:
        assert query not in stream.getvalue()


@pytest.mark.parametrize("query", ["x" * 101, "\U0001f600" * 101, "\x00"])
def test_search_rejects_malformed_or_unbounded_parameters(membership_client, query):
    client, d, _ = membership_client
    shared(send(client, d, "list", params={"q": query}), d, "list", 400)
    d.store.list.assert_not_called()


def test_list_rejects_duplicate_search_parameters(membership_client):
    client, d, _ = membership_client
    shared(send(client, d, "list", url=path(d, "list") + "?q=a&q=b"), d, "list", 400)
    d.store.list.assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS)
def test_member_endpoints_reject_malformed_workspace_uuid(membership_client, operation):
    client, d, _ = membership_client
    url = path(d, operation).replace(str(d.workspace_id), "invalid-uuid")
    shared(send(client, d, operation, url=url), d, operation, 422)
    getattr(d.store, operation).assert_not_called()


def test_remove_rejects_malformed_target_uuid(membership_client):
    client, d, _ = membership_client
    url = path(d, "remove").replace(str(d.user_id), "invalid-uuid")
    shared(send(client, d, "remove", url=url), d, "remove", 422)
    d.store.remove.assert_not_called()


def test_owner_repeat_removal_is_bodyless_and_leave_repeat_is_not_found(membership_client):
    client, d, _ = membership_client
    for _ in range(2):
        shared(send(client, d, "remove"), d, "remove", 204)
    d.store.leave.side_effect = [None, APIError(404)]
    shared(send(client, d, "leave"), d, "leave", 204)
    shared(send(client, d, "leave"), d, "leave", 404)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_member_routes_support_public_cors(membership_client, operation):
    client, d, _ = membership_client
    response = client.options(path(d, operation), headers={
        "Origin": d.settings.allowed_origins[0],
        "Access-Control-Request-Method": "GET" if operation == "list" else "DELETE",
        "Access-Control-Request-Headers": "authorization",
    })
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == d.settings.allowed_origins[0]
