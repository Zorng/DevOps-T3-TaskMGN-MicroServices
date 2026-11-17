from datetime import datetime, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from starlette.datastructures import QueryParams

from identity_workspace.auth.service import token_hash
from identity_workspace.correlation import request_id_context
from identity_workspace.errors import APIError
from identity_workspace.invitations.models import InviteCreate, InvitationResponse, created_event, transition
from identity_workspace.invitations.routes import pagination
from identity_workspace.invitations.service import InvitationService
from identity_workspace.invitations.store import fingerprint
from tests.workspace_contract import event_contract

OPERATIONS = ["create", "list_owned", "list_received", "read", "respond", "cancel"]


def call(service, operation, d):
    args = {
        "create": (d.workspace_id, "recipient@example.com", d.key),
        "list_owned": (d.workspace_id, 20, None, "pending"), "list_received": (20, None, "pending"),
        "read": (d.identifier,), "respond": (d.identifier, "accept"), "cancel": (d.identifier,),
    }
    return getattr(service, operation)("S" * 43, *args[operation])


@pytest.mark.parametrize("operation", OPERATIONS)
def test_each_invitation_operation_hashes_session_and_returns_store_result(invitation_dependencies, operation):
    d = invitation_dependencies
    assert call(InvitationService(d.store), operation, d) == getattr(d.store, operation).return_value
    assert getattr(d.store, operation).call_args.args[0] == token_hash("S" * 43)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("status,code", [(401, "SESSION_INVALID"), (404, "NOT_FOUND")])
def test_every_operation_preserves_authentication_and_visibility_errors(invitation_dependencies, operation, status, code):
    d = invitation_dependencies
    getattr(d.store, operation).side_effect = APIError(status, code=code)
    with pytest.raises(APIError) as caught:
        call(InvitationService(d.store), operation, d)
    assert (caught.value.status_code, caught.value.code) == (status, code)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_every_invitation_operation_hides_database_errors(invitation_dependencies, operation):
    d = invitation_dependencies
    getattr(d.store, operation).side_effect = SQLAlchemyError("synthetic-private-database-credential")
    with pytest.raises(APIError) as caught:
        call(InvitationService(d.store), operation, d)
    assert caught.value.status_code == 503 and "synthetic-private" not in str(caught.value)


def test_create_lock_timeout_is_retryable_without_consuming_a_key(invitation_dependencies):
    d = invitation_dependencies
    original = Exception("synthetic-private-lock-detail")
    original.sqlstate = "55P03"
    d.store.create.side_effect = DBAPIError(None, None, original)
    with pytest.raises(APIError) as caught:
        call(InvitationService(d.store), "create", d)
    assert caught.value.code == "IDEMPOTENCY_IN_PROGRESS" and caught.value.retry_after == 1


@pytest.mark.parametrize("current", ["pending", "accepted", "declined", "cancelled"])
@pytest.mark.parametrize("requested", ["accepted", "declined", "cancelled"])
def test_terminal_transition_matrix(current, requested):
    if current == "pending":
        assert transition(current, requested) is True
    elif current == requested:
        assert transition(current, requested) is False
    else:
        with pytest.raises(APIError) as caught:
            transition(current, requested)
        assert caught.value.code == "INVITATION_NOT_PENDING"


@pytest.mark.parametrize("value", [None, 1, True, "", " \t\n ", "invalid", "a@@example.com", "a" * 255, "a\x00@example.com"])
def test_invitation_email_requires_bounded_valid_text(value):
    with pytest.raises(ValidationError):
        InviteCreate(email=value)


def test_email_normalization_and_fingerprint_preserve_dots_and_plus_aliases():
    body = InviteCreate(email=" \tUser.Name+tag@EXAMPLE.COM\n ")
    assert body.email == "user.name+tag@example.com"
    assert fingerprint(body.email) == fingerprint(InviteCreate(email="USER.NAME+TAG@example.com").email)
    assert fingerprint(body.email) != fingerprint("username@example.com")
    assert body.email not in repr(body)
    with pytest.raises(ValidationError):
        InviteCreate(email="valid@example.com", recipient_id=str(uuid4()))


@pytest.mark.parametrize("body", [{}, {"action": "cancel"}, {"action": None}, {"action": True}, {"action": "ACCEPT"}, {"action": "accept", "extra": 1}])
def test_response_accepts_only_the_two_exact_actions(body):
    with pytest.raises(ValidationError):
        InvitationResponse(**body)


@pytest.mark.parametrize("query,limit,cursor,status,code", [
    ("limit=1&limit=2", "2", None, None, "MALFORMED_REQUEST"),
    ("status=pending&status=accepted", "20", None, "accepted", "MALFORMED_REQUEST"),
    ("", "0", None, None, "MALFORMED_REQUEST"), ("", "101", None, None, "MALFORMED_REQUEST"),
    ("", "1.0", None, None, "MALFORMED_REQUEST"), ("", "20", None, "expired", "MALFORMED_REQUEST"),
    ("", "20", "x" * 1025, None, "INVALID_CURSOR"),
])
def test_list_parameter_validation(query, limit, cursor, status, code):
    with pytest.raises(APIError) as caught:
        pagination(SimpleNamespace(query_params=QueryParams(query)), limit, cursor, status)
    assert caught.value.code == code


def test_creation_event_uses_invitation_aggregate_and_excludes_email_and_credentials():
    identifier, workspace_id, recipient_id, request_id = uuid4(), uuid4(), uuid4(), str(uuid4())
    actor = {"id": uuid4(), "display_name": "Owner", "email": "private@example.com", "provider_subject": "private-subject"}
    context = request_id_context.set(request_id)
    try:
        event = created_event(identifier, workspace_id, recipient_id, actor, datetime.now(timezone.utc))
    finally:
        request_id_context.reset(context)
    event_contract(event)
    assert event["aggregate_id"] == event["data"]["invitation_id"] == str(identifier)
    assert event["aggregate_version"] == 1 and event["request_id"] == request_id
    assert "private" not in json.dumps(event)
