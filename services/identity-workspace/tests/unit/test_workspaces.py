from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from identity_workspace.auth.service import token_hash
from identity_workspace.correlation import request_id_context
from identity_workspace.errors import APIError
from identity_workspace.workspaces.models import WorkspaceNameBody, deletion_event, require_match
from identity_workspace.workspaces.routes import idempotency_key
from identity_workspace.workspaces.service import WorkspaceService
from identity_workspace.workspaces.store import fingerprint
from tests.workspace_contract import event_contract


def call(service, operation, d):
    args = {
        "list": (20, None), "create": ("Team", d.key), "read": (d.identifier,),
        "rename": (d.identifier, "Renamed", ['"1"']), "delete": (d.identifier, ['"1"']),
    }
    return getattr(service, operation)("S" * 43, *args[operation])


@pytest.mark.parametrize("operation", ["list", "create", "read", "rename", "delete"])
def test_each_operation_passes_only_session_hash_to_its_store(workspace_dependencies, operation):
    d = workspace_dependencies
    result = call(WorkspaceService(d.store), operation, d)
    assert result == getattr(d.store, operation).return_value
    assert getattr(d.store, operation).call_args.args[0] == token_hash("S" * 43)


@pytest.mark.parametrize("operation", ["list", "create", "read", "rename", "delete"])
@pytest.mark.parametrize("code,status", [("SESSION_INVALID", 401), ("NOT_FOUND", 404)])
def test_each_operation_preserves_authentication_and_visibility_errors(workspace_dependencies, operation, code, status):
    d = workspace_dependencies
    getattr(d.store, operation).side_effect = APIError(status, code=code)
    with pytest.raises(APIError) as caught:
        call(WorkspaceService(d.store), operation, d)
    assert (caught.value.status_code, caught.value.code) == (status, code)


@pytest.mark.parametrize("operation", ["list", "create", "read", "rename", "delete"])
def test_each_operation_hides_database_failure_details(workspace_dependencies, operation):
    d = workspace_dependencies
    getattr(d.store, operation).side_effect = SQLAlchemyError("synthetic-sensitive-dsn")
    with pytest.raises(APIError) as caught:
        call(WorkspaceService(d.store), operation, d)
    assert caught.value.status_code == 503
    assert "synthetic-sensitive" not in str(caught.value)


def test_create_lock_timeout_allows_safe_retry(workspace_dependencies):
    d = workspace_dependencies
    failure = Exception("synthetic-sensitive-lock-detail")
    failure.sqlstate = "55P03"
    d.store.create.side_effect = DBAPIError(None, None, failure)
    with pytest.raises(APIError) as caught:
        call(WorkspaceService(d.store), "create", d)
    assert caught.value.code == "IDEMPOTENCY_IN_PROGRESS" and caught.value.retry_after == 1


@pytest.mark.parametrize("values,status", [
    ([], 428), (['1'], 400), (['W/"1"'], 400), (['*'], 400), (['"01"'], 400),
    (['"1", "2"'], 400), (['"1"', '"1"'], 400), (['"0"'], 400),
    (['"-1"'], 400), (['"2"'], 412), (['"' + '9' * 5000 + '"'], 412),
])
def test_exact_preconditions(values, status):
    with pytest.raises(APIError) as caught:
        require_match(values, 1)
    assert caught.value.status_code == status


def test_matching_precondition_is_accepted():
    assert require_match(['"1"'], 1) is None


@pytest.mark.parametrize("name", ["", " \n\t", "x" * 101, None, True, 123])
def test_both_write_bodies_reject_invalid_names(name):
    with pytest.raises(ValidationError):
        WorkspaceNameBody(name=name)


def test_normalization_counts_unicode_codepoints_and_rejects_unknown_fields():
    normalized = WorkspaceNameBody(name=" \t" + "\U0001f600" * 100 + "\n ").name
    assert len(normalized) == 100
    assert fingerprint(normalized) == fingerprint(WorkspaceNameBody(name=normalized).name)
    assert fingerprint("Team") != fingerprint("team")
    with pytest.raises(ValidationError):
        WorkspaceNameBody(name="Team", owner_id=str(uuid4()))


@pytest.mark.parametrize("values,code", [([], "IDEMPOTENCY_KEY_REQUIRED"), (["bad"], "MALFORMED_REQUEST"), ([str(uuid4())] * 2, "MALFORMED_REQUEST")])
def test_create_key_validation(values, code):
    request = SimpleNamespace(headers=Mock())
    request.headers.getlist.return_value = values
    with pytest.raises(APIError) as caught:
        idempotency_key(request)
    assert caught.value.code == code


def test_deletion_event_matches_shared_contract_and_redacts_private_profile():
    actor = {"id": uuid4(), "display_name": "Owner", "email": "private@example.com", "provider_subject": "private-subject"}
    request_id = str(uuid4())
    context = request_id_context.set(request_id)
    try:
        event = deletion_event(uuid4(), 2, actor, datetime.now(timezone.utc))
    finally:
        request_id_context.reset(context)
    event_contract(event)
    assert event["request_id"] == request_id
    assert "private" not in json.dumps(event)
    assert len(json.dumps(event).encode("utf-8")) < 65536
