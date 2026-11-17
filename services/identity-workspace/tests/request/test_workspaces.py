from io import StringIO
from unittest.mock import Mock
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.errors import APIError
from identity_workspace.main import create_app
from tests.workspace_contract import public_contract

BASE = "/api/v1/workspaces"
BEARER = {"Authorization": "Bearer " + "S" * 43}
OPERATIONS = ["list", "create", "read", "rename", "delete"]


@pytest.fixture
def workspace_client(workspace_dependencies):
    d = workspace_dependencies
    stream = StringIO()
    app = create_app(d.settings, database_factory=Mock(), google_verifier_factory=Mock(),
                     workspace_store_factory=Mock(return_value=d.store), log_stream=stream)
    with TestClient(app) as client:
        yield client, d, stream


def send(client, d, operation, *, headers=None, path=None, **overrides):
    method = {"list": "GET", "create": "POST", "read": "GET", "rename": "PATCH", "delete": "DELETE"}[operation]
    url = path or (BASE if operation in {"list", "create"} else f"{BASE}/{d.identifier}")
    options = {}
    if operation in {"create", "rename"}:
        options["json"] = {"name": "Team" if operation == "create" else "Renamed"}
    options.update(overrides)
    normal = {**BEARER, "Idempotency-Key": str(d.key), "If-Match": '"1"'}
    return client.request(method, url, headers=normal if headers is None else headers, **options)


def shared(response, status):
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
    if status >= 400:
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize("operation,status", [("list", 200), ("create", 201), ("read", 200), ("rename", 200), ("delete", 204)])
def test_each_endpoint_success_matches_contract_and_safe_logging(workspace_client, operation, status):
    client, d, stream = workspace_client
    response = send(client, d, operation)
    shared(response, status)
    if operation == "delete":
        assert response.content == b""
    else:
        public_contract(response.json(), "WorkspacePage" if operation == "list" else "Workspace")
    if operation in {"create", "read", "rename"}:
        assert response.headers["etag"] == ('"2"' if operation == "rename" else '"1"')
    assert "S" * 43 not in stream.getvalue()
    assert str(d.key) not in stream.getvalue()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("headers,code", [({}, "AUTH_REQUIRED"), ({"Authorization": "Bearer bad"}, "SESSION_INVALID")])
def test_all_workspace_endpoints_require_valid_bearer_syntax(workspace_client, operation, headers, code):
    client, d, _ = workspace_client
    response = send(client, d, operation, headers=headers)
    shared(response, 401)
    assert response.json()["error"]["code"] == code
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_all_workspace_endpoints_reject_invalid_sessions(workspace_client, operation, state):
    client, d, _ = workspace_client
    getattr(d.store, operation).side_effect = APIError(401, code="SESSION_INVALID")
    response = send(client, d, operation)
    shared(response, 401)
    assert response.json()["error"]["code"] == "SESSION_INVALID"


@pytest.mark.parametrize("operation", OPERATIONS)
def test_all_workspace_endpoints_hide_dependency_failures(workspace_client, operation):
    client, d, stream = workspace_client
    getattr(d.store, operation).side_effect = SQLAlchemyError("synthetic-sensitive-password")
    response = send(client, d, operation)
    shared(response, 503)
    assert "synthetic-sensitive" not in response.text + stream.getvalue()


@pytest.mark.parametrize("operation", ["create", "rename"])
@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": " \t\n"}, {"name": "x" * 101}, {"name": 2}, {"name": None}, {"name": "Team", "secret-field": "private-input"}])
def test_names_are_strict_bounded_and_private(workspace_client, operation, body):
    client, d, stream = workspace_client
    response = send(client, d, operation, json=body)
    shared(response, 422)
    assert "secret-field" not in response.text and "private-input" not in response.text + stream.getvalue()
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", ["create", "rename"])
def test_write_names_are_trimmed_before_persistence(workspace_client, operation):
    client, d, _ = workspace_client
    send(client, d, operation, json={"name": " \tTeam\n "})
    args = getattr(d.store, operation).call_args.args
    assert args[1 if operation == "create" else 2] == "Team"


@pytest.mark.parametrize("operation", OPERATIONS)
def test_all_endpoints_reject_unknown_queries(workspace_client, operation):
    client, d, _ = workspace_client
    path = BASE if operation in {"list", "create"} else f"{BASE}/{d.identifier}"
    response = send(client, d, operation, path=path + "?secret-query=private-input")
    shared(response, 400)
    assert "private-input" not in response.text
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("operation", ["list", "read", "delete"])
def test_bodyless_operations_reject_body(workspace_client, operation):
    client, d, _ = workspace_client
    shared(send(client, d, operation, json={}), 400)
    getattr(d.store, operation).assert_not_called()


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=abc", "limit=1.0", "limit=2&limit=3", "cursor=a&cursor=b"])
def test_list_rejects_invalid_or_duplicate_parameters(workspace_client, query):
    client, d, _ = workspace_client
    shared(send(client, d, "list", path=BASE + "?" + query), 400)
    d.store.list.assert_not_called()


def test_list_passes_limit_and_opaque_cursor(workspace_client):
    client, d, _ = workspace_client
    shared(send(client, d, "list", path=BASE + "?limit=5&cursor=" + "C" * 43), 200)
    assert d.store.list.call_args.args[1:] == (5, "C" * 43)


@pytest.mark.parametrize("operation", ["rename", "delete"])
@pytest.mark.parametrize("status,code", [(404, "NOT_FOUND"), (403, "FORBIDDEN"), (412, "VERSION_CONFLICT"), (428, "PRECONDITION_REQUIRED")])
def test_entity_endpoints_preserve_access_and_precondition_errors(workspace_client, operation, status, code):
    client, d, _ = workspace_client
    getattr(d.store, operation).side_effect = APIError(status, code=code)
    response = send(client, d, operation)
    shared(response, status)
    assert response.json()["error"]["code"] == code


def test_read_inaccessible_workspace_is_not_found(workspace_client):
    client, d, _ = workspace_client
    d.store.read.side_effect = APIError(404)
    shared(send(client, d, "read"), 404)


@pytest.mark.parametrize("headers,status,code", [
    (BEARER, 400, "IDEMPOTENCY_KEY_REQUIRED"),
    ({**BEARER, "Idempotency-Key": "bad"}, 400, "MALFORMED_REQUEST"),
])
def test_create_requires_uuid_idempotency_key(workspace_client, headers, status, code):
    client, d, _ = workspace_client
    response = send(client, d, "create", headers=headers)
    shared(response, status)
    assert response.json()["error"]["code"] == code
    d.store.create.assert_not_called()


@pytest.mark.parametrize("code", ["WORKSPACE_LIMIT_REACHED", "IDEMPOTENCY_CONFLICT", "IDEMPOTENCY_IN_PROGRESS"])
def test_create_contract_conflicts(workspace_client, code):
    client, d, _ = workspace_client
    d.store.create.side_effect = APIError(409, code=code, retry_after=1)
    response = send(client, d, "create")
    shared(response, 409)
    assert response.json()["error"]["code"] == code
    if code == "IDEMPOTENCY_IN_PROGRESS":
        assert response.headers["retry-after"] == "1"


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_raw_if_match_is_checked_after_access_in_store(workspace_client, operation):
    client, d, _ = workspace_client
    shared(send(client, d, operation, headers=BEARER), 200 if operation == "rename" else 204)
    assert getattr(d.store, operation).call_args.args[-1] == []


def test_workspace_preflight_supports_idempotency_and_if_match(workspace_client):
    client, d, _ = workspace_client
    origin = d.settings.allowed_origins[0]
    response = client.options(f"{BASE}/{d.identifier}", headers={
        "Origin": origin, "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "authorization,content-type,if-match",
    })
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == origin
    assert "etag" in response.headers["access-control-expose-headers"].lower()
