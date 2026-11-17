"""Membership permissions, races and generation-specific events on real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
import json
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from identity_workspace.auth.service import token_hash
from identity_workspace.auth.store import database_now, session_actor
from identity_workspace.errors import APIError
from identity_workspace.main import create_app
from identity_workspace.memberships.primitives import access_snapshot, join_member, locked_workspace
from identity_workspace.memberships.service import MembershipService
from identity_workspace.memberships.store import MembershipStore
from identity_workspace.persistence import invitations, memberships, outbox, sessions, users, workspaces
from identity_workspace.workspaces.models import MAX_VERSION
from identity_workspace.workspaces.service import WorkspaceService
from identity_workspace.workspaces.store import WorkspaceStore
from tests.workspace_contract import event_contract, private_contract, public_contract


@pytest.fixture
def member_data(database, registered_actor):
    owner = registered_actor("Example Owner")
    member = registered_actor("Alice Example", "private.lookup@example.com")
    outsider = registered_actor("Outsider")
    workspace_service = WorkspaceService(WorkspaceStore(database))
    body, _ = workspace_service.create(owner.token, "Membership verification", uuid4())
    identifier = UUID(body["id"])

    def join(actor, workspace_id=identifier):
        with database.transaction() as session:
            session_actor(session, token_hash(actor.token), lock=True)
            return join_member(session, workspace_id, actor.id)

    generation = join(member)
    store = MembershipStore(database)
    return SimpleNamespace(database=database, actor=registered_actor, owner=owner, member=member,
                           outsider=outsider, workspace_id=identifier, generation=generation,
                           workspace_service=workspace_service, store=store,
                           service=MembershipService(store), join=join)


@pytest.fixture
def member_http(member_data, postgres):
    d = member_data
    stream = StringIO()
    app = create_app(postgres.settings(), database_factory=lambda _: d.database,
                     google_verifier_factory=Mock(), log_stream=stream)
    with TestClient(app) as client:
        yield client, d, stream


def paths(d):
    root = f"/api/v1/workspaces/{d.workspace_id}"
    return SimpleNamespace(workspace=root, list=root + "/members", remove=root + "/members/",
                           leave=root + "/membership")


def rows(d, table):
    with d.database.transaction() as session:
        return session.execute(select(table)).mappings().all()


def error(status, code, operation, *args):
    with pytest.raises(APIError) as caught:
        operation(*args)
    assert (caught.value.status_code, caught.value.code) == (status, code)


def test_current_owner_and_member_list_contract_order_and_private_safe_fields(member_http):
    client, d, _ = member_http
    for actor in (d.owner, d.member):
        response = client.get(paths(d).list, headers=actor.headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        public_contract(response.json(), "MemberList")
        assert [m["user"]["id"] for m in response.json()["items"]] == [str(d.owner.id), str(d.member.id)]
        assert response.json()["items"][1]["membership_id"] == str(d.generation)
        assert set(response.json()["items"][1]["user"]) == {"id", "display_name"}
        assert "private.lookup@example.com" not in response.text


def test_member_search_is_literal_case_insensitive_bounded_and_not_an_email_directory(member_http):
    client, d, stream = member_http
    unusual = d.actor("aLiCe%_Name")
    slash = d.actor("Slash / name")
    d.join(unusual)
    d.join(slash)
    for query, expected in [
        ("ALICE", {d.member.id, unusual.id}), ("%", {unusual.id}), ("_", {unusual.id}),
        ("%_", {unusual.id}), ("/", {slash.id}), ("private.lookup@example.com", set()),
        ("'; DROP TABLE users; --", set()), ("\U0001f600" * 100, set()),
    ]:
        response = client.get(paths(d).list, headers=d.member.headers, params={"q": query})
        assert response.status_code == 200
        assert {UUID(m["user"]["id"]) for m in response.json()["items"]} == expected
    assert client.get(paths(d).list, headers=d.member.headers, params={"q": "x" * 101}).status_code == 400
    assert "private.lookup@example.com" not in stream.getvalue()
    assert len(rows(d, users)) == 5


def test_equal_join_timestamps_use_membership_id_as_ascending_tiebreaker(member_data, monkeypatch):
    d = member_data
    with d.database.transaction() as session:
        fixed = database_now(session)
    monkeypatch.setattr("identity_workspace.memberships.primitives.database_now", lambda _: fixed)
    for _ in range(3):
        d.join(d.actor())
    expected = sorted(rows(d, memberships), key=lambda row: (row["joined_at"], row["id"].int))
    result = d.service.list(d.owner.token, d.workspace_id)["items"]
    assert [m["membership_id"] for m in result] == [str(m["id"]) for m in expected]


def test_join_primitive_caps_twenty_and_leaving_releases_member_slot_without_owned_quota_changes(member_data):
    d = member_data
    for _ in range(18):
        d.join(d.actor())
    assert len(d.service.list(d.owner.token, d.workspace_id)["items"]) == 20
    candidate = d.actor()
    error(409, "MEMBER_LIMIT_REACHED", d.join, candidate)
    error(409, "ALREADY_MEMBER", d.join, d.member)
    d.service.leave(d.member.token, d.workspace_id)
    d.join(candidate)
    assert len(d.service.list(d.owner.token, d.workspace_id)["items"]) == 20
    assert next(u for u in rows(d, users) if u["id"] == d.owner.id)["owned_workspace_count"] == 1
    assert all(u["owned_workspace_count"] == 0 for u in rows(d, users) if u["id"] != d.owner.id)


@pytest.mark.parametrize("operation", ["list", "remove", "leave"])
def test_nonmembers_and_revoked_members_receive_404_for_all_member_endpoints(member_http, operation):
    client, d, _ = member_http
    for actor in (d.outsider,):
        url = {"list": paths(d).list, "remove": paths(d).remove + str(d.member.id), "leave": paths(d).leave}[operation]
        assert client.request("GET" if operation == "list" else "DELETE", url, headers=actor.headers).status_code == 404
    d.service.remove(d.owner.token, d.workspace_id, d.member.id)
    url = {"list": paths(d).list, "remove": paths(d).remove + str(d.owner.id), "leave": paths(d).leave}[operation]
    assert client.request("GET" if operation == "list" else "DELETE", url, headers=d.member.headers).status_code == 404


@pytest.mark.parametrize("target", ["owner", "self", "unknown"])
def test_nonowners_cannot_remove_any_target_even_themselves(member_http, target):
    client, d, _ = member_http
    identifier = {"owner": d.owner.id, "self": d.member.id, "unknown": uuid4()}[target]
    response = client.delete(paths(d).remove + str(identifier), headers=d.member.headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
    assert rows(d, outbox) == []


@pytest.mark.parametrize("operation", ["remove", "leave"])
def test_owner_cannot_leave_or_be_removed_and_state_is_preserved(member_http, operation):
    client, d, _ = member_http
    before, etag = d.workspace_service.read(d.owner.token, d.workspace_id)
    url = paths(d).remove + str(d.owner.id) if operation == "remove" else paths(d).leave
    response = client.delete(url, headers=d.owner.headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "OWNER_CANNOT_LEAVE"
    assert d.workspace_service.read(d.owner.token, d.workspace_id) == (before, etag)
    assert rows(d, outbox) == []


@pytest.mark.parametrize("operation,reason", [("remove", "removed"), ("leave", "left")])
def test_revocation_event_access_etag_and_repeat_semantics(member_http, operation, reason):
    client, d, stream = member_http
    before, etag = d.workspace_service.read(d.owner.token, d.workspace_id)
    request_id = str(uuid4())
    actor = d.owner if operation == "remove" else d.member
    url = paths(d).remove + str(d.member.id) if operation == "remove" else paths(d).leave
    response = client.delete(url, headers={**actor.headers, "X-Request-ID": request_id})
    assert response.status_code == 204 and response.content == b""
    after, new_etag = d.workspace_service.read(d.owner.token, d.workspace_id)
    assert after["member_count"] == 1 and after["version"] == before["version"] + 1
    assert new_etag != etag
    saved, = rows(d, outbox)
    event_contract(saved["payload"])
    assert saved["event_type"] == "workspace.member_removed.v1" and saved["published_at"] is None
    assert saved["payload"]["data"] == {
        "workspace_id": str(d.workspace_id), "user_id": str(d.member.id),
        "membership_id": str(d.generation), "reason": reason,
        "actor": {"id": str(actor.id), "display_name": actor.display_name},
    }
    assert saved["payload"]["request_id"] == request_id
    assert saved["payload"]["aggregate_version"] == after["version"]
    assert client.get(paths(d).workspace, headers=d.member.headers).status_code == 404
    assert client.get(paths(d).list, headers=d.member.headers).status_code == 404
    assert client.get("/api/v1/workspaces", headers=d.member.headers).json()["items"] == []
    assert client.get("/api/v1/me", headers=d.member.headers).status_code == 200
    assert client.delete(url, headers=actor.headers).status_code == (204 if operation == "remove" else 404)
    assert client.delete(paths(d).remove + str(uuid4()), headers=d.owner.headers).status_code == 204
    assert d.workspace_service.read(d.owner.token, d.workspace_id) == (after, new_etag)
    assert len(rows(d, outbox)) == 1
    error(412, "VERSION_CONFLICT", d.workspace_service.rename, d.owner.token, d.workspace_id, "Changed", [etag])
    assert "private.lookup@example.com" not in json.dumps(saved["payload"]) + stream.getvalue()
    assert d.owner.token not in stream.getvalue() and d.member.token not in stream.getvalue()
    assert client.get("/health/ready").status_code == 200


@pytest.mark.parametrize("operation", ["list", "remove", "leave"])
@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_real_session_expiry_and_logout_block_all_member_endpoints(member_http, operation, state):
    client, d, _ = member_http
    with d.database.transaction() as session:
        now = database_now(session)
        values = {"revoked_at": now} if state == "revoked" else {
            "created_at": now - timedelta(hours=9), "expires_at": now - timedelta(hours=1),
        }
        session.execute(update(sessions).where(sessions.c.user_id == d.owner.id).values(**values))
    url = {"list": paths(d).list, "remove": paths(d).remove + str(d.member.id), "leave": paths(d).leave}[operation]
    response = client.request("GET" if operation == "list" else "DELETE", url, headers=d.owner.headers)
    assert response.status_code == 401 and response.json()["error"]["code"] == "SESSION_INVALID"
    assert rows(d, outbox) == []


@pytest.mark.parametrize("operation", ["list", "remove", "leave"])
def test_workspace_deletion_overrides_membership_visibility(member_data, operation):
    d = member_data
    _, etag = d.workspace_service.read(d.owner.token, d.workspace_id)
    d.workspace_service.delete(d.owner.token, d.workspace_id, [etag])
    args = (d.member.id,) if operation == "remove" else ()
    error(404, "NOT_FOUND", getattr(d.service, operation), d.owner.token, d.workspace_id, *args)


@pytest.mark.parametrize("operation", ["remove", "leave"])
@pytest.mark.parametrize("failed_write", ["UPDATE identity_workspace.workspaces", "UPDATE identity_workspace.memberships", "INSERT INTO identity_workspace.outbox"])
def test_revocation_rolls_back_membership_version_and_event_on_any_write_failure(member_data, operation, failed_write):
    d = member_data
    before = d.workspace_service.read(d.owner.token, d.workspace_id)
    def fail(connection, cursor, statement, parameters, context, many):
        if failed_write in statement:
            raise SQLAlchemyError("synthetic-private-membership-failure")
    event.listen(d.database.engine, "before_cursor_execute", fail)
    try:
        args = (d.owner.token, d.workspace_id, d.member.id) if operation == "remove" else (d.member.token, d.workspace_id)
        error(503, "DEPENDENCY_UNAVAILABLE", getattr(d.service, operation), *args)
    finally:
        event.remove(d.database.engine, "before_cursor_execute", fail)
    assert d.workspace_service.read(d.owner.token, d.workspace_id) == before
    assert next(m for m in rows(d, memberships) if m["id"] == d.generation)["revoked_at"] is None
    assert rows(d, outbox) == []


@pytest.mark.parametrize("operation", ["remove", "leave"])
def test_rejoin_creates_new_immutable_generation_and_old_event_cannot_target_it(member_data, operation):
    d = member_data
    if operation == "remove":
        d.service.remove(d.owner.token, d.workspace_id, d.member.id)
    else:
        d.service.leave(d.member.token, d.workspace_id)
    old_payload = rows(d, outbox)[0]["payload"]
    replacement = d.join(d.member)
    assert replacement != d.generation
    history = [m for m in rows(d, memberships) if m["user_id"] == d.member.id]
    assert len(history) == 2
    assert next(m for m in history if m["id"] == d.generation)["revoked_at"] is not None
    assert next(m for m in history if m["id"] == replacement)["revoked_at"] is None
    snapshot = d.service.access_snapshot(d.member.token, d.workspace_id)
    private_contract(snapshot, "AccessContext")
    assert snapshot["actor_membership_id"] == str(replacement)
    assert str(d.generation) not in json.dumps(snapshot)
    assert old_payload == rows(d, outbox)[0]["payload"]
    assert old_payload["data"]["membership_id"] != snapshot["actor_membership_id"]


@pytest.mark.parametrize("column", ["id", "workspace_id", "user_id", "role", "joined_at"])
def test_database_rejects_changes_to_membership_generation_identity(member_data, column):
    d = member_data
    second, _ = d.workspace_service.create(d.owner.token, "Another", uuid4())
    values = {
        "id": uuid4(), "workspace_id": UUID(second["id"]), "user_id": d.outsider.id,
        "role": "owner", "joined_at": rows(d, memberships)[0]["joined_at"] + timedelta(days=1),
    }
    with pytest.raises(IntegrityError):
        with d.database.transaction() as session:
            session.execute(update(memberships).where(memberships.c.id == d.generation).values({column: values[column]}))
    assert next(m for m in rows(d, memberships) if m["id"] == d.generation)["user_id"] == d.member.id


@pytest.mark.parametrize("change", ["restore", "retimestamp"])
def test_database_rejects_reactivation_or_rewriting_of_revoked_generation(member_data, change):
    d = member_data
    d.service.leave(d.member.token, d.workspace_id)
    revoked = next(m for m in rows(d, memberships) if m["id"] == d.generation)
    value = None if change == "restore" else revoked["revoked_at"] + timedelta(seconds=1)
    with pytest.raises(IntegrityError):
        with d.database.transaction() as session:
            session.execute(update(memberships).where(memberships.c.id == d.generation).values(revoked_at=value))


def test_join_rolls_back_with_invitation_acceptance_in_callers_transaction(member_data):
    d = member_data
    invite_id = uuid4()
    with d.database.transaction() as session:
        now = database_now(session)
        session.execute(invitations.insert().values(
            id=invite_id, workspace_id=d.workspace_id, inviter_id=d.owner.id,
            recipient_id=d.outsider.id, status="pending", created_at=now, updated_at=now,
        ))
    before = d.workspace_service.read(d.owner.token, d.workspace_id)
    def fail(connection, cursor, statement, parameters, context, many):
        if "UPDATE identity_workspace.workspaces" in statement:
            raise SQLAlchemyError("synthetic-invitation-acceptance-failure")
    event.listen(d.database.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(SQLAlchemyError):
            with d.database.transaction() as session:
                session_actor(session, token_hash(d.outsider.token), lock=True)
                locked_workspace(session, d.workspace_id, write=True)
                session.execute(update(invitations).where(invitations.c.id == invite_id).values(status="accepted"))
                join_member(session, d.workspace_id, d.outsider.id)
    finally:
        event.remove(d.database.engine, "before_cursor_execute", fail)
    assert rows(d, invitations)[0]["status"] == "pending"
    assert not any(m["user_id"] == d.outsider.id for m in rows(d, memberships))
    assert d.workspace_service.read(d.owner.token, d.workspace_id) == before


@pytest.mark.parametrize("race", ["two_removes", "two_leaves", "remove_leave"])
def test_competing_revocations_emit_one_generation_event_and_one_version_increment(member_data, race):
    d = member_data
    before, _ = d.workspace_service.read(d.owner.token, d.workspace_id)
    barrier = Barrier(2)
    def attempt(operation):
        barrier.wait(timeout=10)
        try:
            if operation == "remove":
                d.service.remove(d.owner.token, d.workspace_id, d.member.id)
            else:
                d.service.leave(d.member.token, d.workspace_id)
            return 204
        except APIError as failure:
            return failure.status_code
    operations = {"two_removes": ["remove", "remove"], "two_leaves": ["leave", "leave"], "remove_leave": ["remove", "leave"]}[race]
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(attempt, operations))
    assert statuses == ([204, 204] if race == "two_removes" else [204, 404]) or (race == "remove_leave" and statuses == [204, 204])
    after, _ = d.workspace_service.read(d.owner.token, d.workspace_id)
    assert after["version"] == before["version"] + 1 and after["member_count"] == 1
    saved, = rows(d, outbox)
    assert saved["payload"]["data"]["membership_id"] == str(d.generation)


def test_concurrent_joins_at_nineteen_members_create_only_one_generation(member_data):
    d = member_data
    for _ in range(17):
        d.join(d.actor())
    candidates = [d.actor(), d.actor()]
    barrier = Barrier(2)
    def join(actor):
        barrier.wait(timeout=10)
        try:
            return d.join(actor)
        except APIError as failure:
            return failure.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(join, candidates))
    assert sum(isinstance(result, UUID) for result in results) == 1
    assert "MEMBER_LIMIT_REACHED" in results
    assert len(d.service.list(d.owner.token, d.workspace_id)["items"]) == 20


def test_snapshot_primitives_are_fresh_and_match_private_contract(member_data):
    d = member_data
    with d.database.transaction() as session:
        snapshot = access_snapshot(session, d.workspace_id, d.member.id)
    private_contract(snapshot, "AccessContext")
    body, _ = d.workspace_service.read(d.owner.token, d.workspace_id)
    assert snapshot["workspace_version"] == body["version"]
    assert snapshot["actor_membership_id"] == str(d.generation)
    assert snapshot["actor_role"] == "member" and "email" not in json.dumps(snapshot)
    d.service.remove(d.owner.token, d.workspace_id, d.member.id)
    error(404, "NOT_FOUND", d.service.access_snapshot, d.member.token, d.workspace_id)
    assert len(d.service.access_snapshot(d.owner.token, d.workspace_id)["members"]) == 1


def test_exhausted_version_refuses_join_or_revocation_without_partial_changes(member_data):
    d = member_data
    with d.database.transaction() as session:
        session.execute(update(workspaces).where(workspaces.c.id == d.workspace_id).values(version=MAX_VERSION))
    error(503, "DEPENDENCY_UNAVAILABLE", d.service.leave, d.member.token, d.workspace_id)
    error(503, "DEPENDENCY_UNAVAILABLE", d.join, d.outsider)
    assert rows(d, outbox) == [] and len(d.service.list(d.owner.token, d.workspace_id)["items"]) == 2


def test_generation_migration_preserves_existing_memberships_and_passes_drift_check(member_data, migrate):
    d = member_data
    migrate("downgrade", "0003_workspace_management")
    before = {m["id"] for m in rows(d, memberships)}
    migrate()
    migrate("check")
    assert {m["id"] for m in rows(d, memberships)} == before
    assert d.database.check_readiness()
