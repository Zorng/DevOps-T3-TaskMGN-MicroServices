from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.auth.service import token_hash
from identity_workspace.correlation import request_id_context
from identity_workspace.errors import APIError
from identity_workspace.memberships.models import removal_event, require_non_owner, require_owner, search_text
from identity_workspace.memberships.routes import service as route_service
from identity_workspace.memberships.service import MembershipService
from identity_workspace.memberships.store import MembershipStore
from tests.workspace_contract import event_contract


def call(service, operation, d):
    args = {
        "list": (d.workspace_id, "Example"), "remove": (d.workspace_id, d.user_id),
        "leave": (d.workspace_id,), "access_snapshot": (d.workspace_id,),
    }
    return getattr(service, operation)("S" * 43, *args[operation])


@pytest.mark.parametrize("operation", ["list", "remove", "leave", "access_snapshot"])
def test_each_operation_hashes_bearer_and_returns_store_result(membership_dependencies, operation):
    d = membership_dependencies
    assert call(MembershipService(d.store), operation, d) == getattr(d.store, operation).return_value
    assert getattr(d.store, operation).call_args.args[0] == token_hash("S" * 43)


@pytest.mark.parametrize("operation", ["list", "remove", "leave", "access_snapshot"])
@pytest.mark.parametrize("status,code", [(401, "SESSION_INVALID"), (404, "NOT_FOUND")])
def test_every_operation_propagates_current_authentication_and_visibility(membership_dependencies, operation, status, code):
    d = membership_dependencies
    getattr(d.store, operation).side_effect = APIError(status, code=code)
    with pytest.raises(APIError) as caught:
        call(MembershipService(d.store), operation, d)
    assert (caught.value.status_code, caught.value.code) == (status, code)


@pytest.mark.parametrize("operation", ["list", "remove", "leave", "access_snapshot"])
def test_database_errors_are_credential_safe(membership_dependencies, operation):
    d = membership_dependencies
    getattr(d.store, operation).side_effect = SQLAlchemyError("synthetic-sensitive-dsn")
    with pytest.raises(APIError) as caught:
        call(MembershipService(d.store), operation, d)
    assert caught.value.status_code == 503
    assert "synthetic-sensitive" not in str(caught.value)


@pytest.mark.parametrize("query", [None, "", "aLiCe", "%_/", " ", "\U0001f600" * 100])
def test_search_accepts_bounded_literal_unicode_without_trimming(query):
    assert search_text(query) == ("" if query is None else query)


@pytest.mark.parametrize("query", ["x" * 101, "\U0001f600" * 101, "\x00", "\ud800", 1, True])
def test_search_rejects_malformed_or_unbounded_parameters_before_database(membership_dependencies, query):
    d = membership_dependencies
    with pytest.raises(APIError) as caught:
        MembershipService(d.store).list("S" * 43, d.workspace_id, query)
    assert caught.value.status_code == 400
    d.store.list.assert_not_called()


@pytest.mark.parametrize("role,is_owner,allowed", [("owner", True, True), ("member", False, False), ("member", True, False), ("owner", False, False)])
def test_owner_permission_requires_both_ownership_and_owner_membership(role, is_owner, allowed):
    actor_id, other_id = uuid4(), uuid4()
    workspace = {"owner_id": actor_id if is_owner else other_id}
    if allowed:
        require_owner(workspace, {"role": role}, actor_id)
    else:
        with pytest.raises(APIError) as caught:
            require_owner(workspace, {"role": role}, actor_id)
        assert caught.value.status_code == 403


@pytest.mark.parametrize("role,is_owner,allowed", [("member", False, True), ("owner", True, False), ("member", True, False), ("owner", False, False)])
def test_owners_cannot_be_removed_or_leave(role, is_owner, allowed):
    owner_id, other_id = uuid4(), uuid4()
    target = {"user_id": owner_id if is_owner else other_id, "role": role}
    if allowed:
        require_non_owner({"owner_id": owner_id}, target)
    else:
        with pytest.raises(APIError) as caught:
            require_non_owner({"owner_id": owner_id}, target)
        assert caught.value.code == "OWNER_CANNOT_LEAVE"


@pytest.mark.parametrize("reason", ["removed", "left"])
def test_event_preserves_exact_generation_reason_and_correlation_without_credentials(reason):
    actor = {"id": uuid4(), "display_name": "Owner", "email": "private@example.com", "provider_subject": "private-subject"}
    generation = {"id": uuid4(), "user_id": uuid4()}
    request_id, workspace_id = str(uuid4()), uuid4()
    context = request_id_context.set(request_id)
    try:
        event = removal_event(workspace_id, 3, generation, actor, reason, datetime.now(timezone.utc))
    finally:
        request_id_context.reset(context)
    event_contract(event)
    assert event["request_id"] == request_id and event["aggregate_version"] == 3
    assert event["data"]["membership_id"] == str(generation["id"])
    assert event["data"]["user_id"] == str(generation["user_id"])
    assert event["data"]["reason"] == reason
    assert "private" not in json.dumps(event)


def test_event_rejects_unknown_reason():
    with pytest.raises(ValueError):
        removal_event(uuid4(), 2, {}, {}, "other", datetime.now(timezone.utc))


def test_uninitialized_route_returns_dependency_error():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(memberships=None)))
    with pytest.raises(APIError) as caught:
        route_service(request)
    assert caught.value.status_code == 503


def test_owner_repeat_removal_of_absent_target_has_no_side_effect(monkeypatch, membership_dependencies):
    d = membership_dependencies
    database = MagicMock()
    store = MembershipStore(database)
    workspace = {"id": d.workspace_id, "owner_id": d.owner_id}
    store._actor_workspace = Mock(return_value=({"id": d.owner_id}, workspace, {"role": "owner"}))
    monkeypatch.setattr("identity_workspace.memberships.store.current_membership", Mock(return_value=None))
    store._revoke = Mock()
    store.remove(token_hash("S" * 43), d.workspace_id, d.user_id)
    store._revoke.assert_not_called()


@pytest.mark.parametrize("operation", ["remove", "leave"])
def test_store_refuses_owner_revocation_before_any_write(monkeypatch, membership_dependencies, operation):
    d = membership_dependencies
    store = MembershipStore(MagicMock())
    workspace = {"id": d.workspace_id, "owner_id": d.owner_id}
    actor = {"id": d.owner_id}
    member = {"id": uuid4(), "user_id": d.owner_id, "role": "owner"}
    store._actor_workspace = Mock(return_value=(actor, workspace, member))
    monkeypatch.setattr("identity_workspace.memberships.store.current_membership", Mock(return_value=member))
    store._revoke = Mock()
    with pytest.raises(APIError) as caught:
        getattr(store, operation)(token_hash("S" * 43), d.workspace_id, *((d.owner_id,) if operation == "remove" else ()))
    assert caught.value.code == "OWNER_CANNOT_LEAVE"
    store._revoke.assert_not_called()
