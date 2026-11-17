from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import timedelta
from io import StringIO
from threading import Event
from unittest.mock import Mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select, text, update

from identity_workspace.auth.service import token_hash
from identity_workspace.auth.store import database_now
from identity_workspace.main import create_app
from identity_workspace.memberships.primitives import access_snapshot, join_member
from identity_workspace.memberships.service import MembershipService
from identity_workspace.memberships.store import MembershipStore
from identity_workspace.persistence import memberships, sessions
from identity_workspace.workspaces.service import WorkspaceService
from identity_workspace.workspaces.store import WorkspaceStore
from tests.workspace_contract import private_contract

INTRO = "/internal/v1/auth/introspect"
TASK_KEY, NOTIFICATION_KEY = "T" * 43, "N" * 43


@pytest.fixture
def private_http(database, registered_actor, postgres):
    owner, member, outsider = registered_actor("Owner"), registered_actor("Member"), registered_actor("Outsider")
    workspaces = WorkspaceService(WorkspaceStore(database))
    workspace_id = UUID(workspaces.create(owner.token, "Private API", uuid4())[0]["id"])
    with database.transaction() as session:
        generation = join_member(session, workspace_id, member.id)
    settings = postgres.settings(task_service_key=TASK_KEY, notification_service_key=NOTIFICATION_KEY)
    stream = StringIO()
    app = create_app(settings, database_factory=lambda _: database, google_verifier_factory=Mock(), log_stream=stream)
    from types import SimpleNamespace
    data = SimpleNamespace(owner=owner, member=member, outsider=outsider, workspace_id=workspace_id, generation=generation,
                           database=database, actor=registered_actor, workspaces=workspaces,
                           memberships=MembershipService(MembershipStore(database)), app=app)
    with TestClient(app) as client:
        yield client, data, stream


def request_headers(actor, caller="task"):
    return {**actor.headers, "X-Service-Key": TASK_KEY if caller == "task" else NOTIFICATION_KEY}


def access_path(d):
    return f"/internal/v1/workspaces/{d.workspace_id}/access"


@pytest.mark.parametrize("caller", ["task", "notification"])
def test_live_session_introspection_has_only_identity_and_expiry(private_http, caller):
    client, d, stream = private_http
    response = client.post(INTRO, headers=request_headers(d.owner, caller))
    assert response.status_code == 200
    private_contract(response.json(), "SessionIdentity")
    assert response.json()["user"] == {"id": str(d.owner.id), "display_name": "Owner"}
    with d.database.transaction() as session:
        expiry = session.scalar(select(sessions.c.expires_at).where(sessions.c.token_hash == token_hash(d.owner.token)))
    assert response.json()["session_expires_at"] == expiry.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert all(value not in stream.getvalue() for value in (d.owner.token, TASK_KEY, NOTIFICATION_KEY))


@pytest.mark.parametrize("role", ["owner", "member"])
def test_task_access_uses_current_role_generation_version_and_members(private_http, role):
    client, d, _ = private_http
    actor = getattr(d, role)
    response = client.post(access_path(d), headers={**request_headers(actor), "X-User-ID": str(d.outsider.id), "X-User-Role": "owner"})
    assert response.status_code == 200
    body = response.json()
    private_contract(body, "AccessContext")
    assert body["actor"] == {"id": str(actor.id), "display_name": actor.display_name}
    assert body["actor_role"] == role
    assert body["workspace_version"] == d.workspaces.read(d.owner.token, d.workspace_id)[0]["version"]
    if role == "member": assert body["actor_membership_id"] == str(d.generation)
    assert len(body["members"]) == 2 and "email" not in response.text and "token" not in response.text


def test_notification_cannot_access_workspace_even_with_claimed_task_headers(private_http):
    client, d, _ = private_http
    response = client.post(access_path(d), headers={**request_headers(d.owner, "notification"), "X-Service-Name": "task"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("endpoint", ["intro", "access"])
@pytest.mark.parametrize("change", ["logout", "expire"])
def test_a_successful_check_never_caches_later_session_revocation_or_expiry(private_http, endpoint, change):
    client, d, _ = private_http
    path = INTRO if endpoint == "intro" else access_path(d)
    assert client.post(path, headers=request_headers(d.owner)).status_code == 200
    if change == "logout":
        assert client.post("/api/v1/auth/logout", headers=d.owner.headers).status_code == 204
    else:
        with d.database.transaction() as session:
            now = database_now(session)
            session.execute(update(sessions).where(sessions.c.token_hash == token_hash(d.owner.token)).values(
                created_at=now - timedelta(hours=9), expires_at=now - timedelta(hours=1)))
    response = client.post(path, headers=request_headers(d.owner))
    assert response.status_code == 401 and response.json()["error"]["code"] == "SESSION_INVALID"


def test_removal_and_rejoin_return_fresh_membership_generations(private_http):
    client, d, _ = private_http
    first = client.post(access_path(d), headers=request_headers(d.member)).json()
    d.memberships.remove(d.owner.token, d.workspace_id, d.member.id)
    assert client.post(access_path(d), headers=request_headers(d.member)).status_code == 404
    assert client.post(INTRO, headers=request_headers(d.member, "notification")).status_code == 200
    owner = client.post(access_path(d), headers=request_headers(d.owner)).json()
    assert len(owner["members"]) == 1 and owner["workspace_version"] > first["workspace_version"]
    with d.database.transaction() as session:
        new_generation = join_member(session, d.workspace_id, d.member.id)
    final = client.post(access_path(d), headers=request_headers(d.member)).json()
    assert final["actor_membership_id"] == str(new_generation) != first["actor_membership_id"]
    assert str(d.generation) not in str(final["members"])


def test_deleted_workspace_and_nonmember_never_receive_access(private_http):
    client, d, _ = private_http
    assert client.post(access_path(d), headers=request_headers(d.outsider)).status_code == 404
    assert client.post(access_path(d), headers=request_headers(d.owner)).status_code == 200
    _, etag = d.workspaces.read(d.owner.token, d.workspace_id)
    d.workspaces.delete(d.owner.token, d.workspace_id, [etag])
    for actor in (d.owner, d.member):
        assert client.post(access_path(d), headers=request_headers(actor)).status_code == 404
        assert client.post(INTRO, headers=request_headers(actor)).status_code == 200


def test_snapshot_is_bounded_to_twenty_and_includes_every_active_generation(private_http):
    client, d, _ = private_http
    for _ in range(18):
        actor = d.actor()
        with d.database.transaction() as session:
            join_member(session, d.workspace_id, actor.id)
    response = client.post(access_path(d), headers=request_headers(d.owner))
    assert response.status_code == 200 and len(response.json()["members"]) == 20
    private_contract(response.json(), "AccessContext")
    with d.database.transaction() as session:
        expected = set(session.scalars(select(memberships.c.id).where(memberships.c.workspace_id == d.workspace_id, memberships.c.revoked_at.is_(None))))
    assert {UUID(member["membership_id"]) for member in response.json()["members"]} == expected


def test_shared_snapshot_lock_serializes_against_membership_revocation(private_http):
    client, d, _ = private_http
    attempted = Event()
    def observe(connection, cursor, statement, parameters, context, many):
        if "identity_workspace.workspaces" in statement and "FOR UPDATE" in statement:
            attempted.set()
    event.listen(d.database.engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with d.database.transaction() as session:
                snapshot = access_snapshot(session, d.workspace_id, d.member.id)
                future = executor.submit(d.memberships.remove, d.owner.token, d.workspace_id, d.member.id)
                assert attempted.wait(timeout=5)
                with pytest.raises(TimeoutError): future.result(timeout=0.1)
                assert snapshot["actor_membership_id"] == str(d.generation)
            future.result(timeout=5)
    finally:
        event.remove(d.database.engine, "before_cursor_execute", observe)
    assert client.post(access_path(d), headers=request_headers(d.member)).status_code == 404


@pytest.mark.parametrize("endpoint", ["intro", "access"])
def test_storage_outage_after_success_fails_closed_without_cached_authorization(private_http, endpoint):
    client, d, stream = private_http
    path = INTRO if endpoint == "intro" else access_path(d)
    assert client.post(path, headers=request_headers(d.owner)).status_code == 200
    from sqlalchemy.exc import SQLAlchemyError
    def fail(*args, **kwargs): raise SQLAlchemyError("synthetic-private-database-details")
    event.listen(d.database.engine, "before_cursor_execute", fail)
    try:
        response = client.post(path, headers=request_headers(d.owner))
    finally:
        event.remove(d.database.engine, "before_cursor_execute", fail)
    assert response.status_code == 503 and response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "synthetic-private" not in response.text + stream.getvalue()


def test_identity_assertions_never_authorize_public_me_without_bearer(private_http):
    client, d, _ = private_http
    response = client.get("/api/v1/me", headers={"X-Service-Key": TASK_KEY, "X-User-ID": str(d.owner.id), "X-User-Role": "owner"})
    assert response.status_code == 401
