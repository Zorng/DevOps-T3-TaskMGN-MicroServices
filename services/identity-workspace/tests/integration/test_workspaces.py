"""Authoritative lifecycle behavior against disposable, real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4
import secrets

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from identity_workspace.auth.service import token_hash
from identity_workspace.auth.store import database_now
from identity_workspace.errors import APIError
from identity_workspace.main import create_app
from identity_workspace.persistence import (
    idempotency_records, invitations, memberships, outbox, sessions, users, workspace_cursors, workspaces,
)
from identity_workspace.workspaces.service import WorkspaceService
from identity_workspace.workspaces.store import WorkspaceStore
from tests.workspace_contract import event_contract, public_contract

BASE = "/api/v1/workspaces"


@pytest.fixture
def workspace_data(database):
    def actor():
        identifier, token = uuid4(), secrets.token_urlsafe(32)
        with database.transaction() as session:
            now = database_now(session)
            session.execute(users.insert().values(
                id=identifier, provider="google", provider_subject=str(uuid4()),
                display_name="Fixture Owner", email=f"{identifier}@example.com", created_at=now,
            ))
            session.execute(sessions.insert().values(
                id=uuid4(), user_id=identifier, token_hash=token_hash(token),
                created_at=now, expires_at=now + timedelta(hours=8),
            ))
        return SimpleNamespace(id=identifier, token=token, headers={"Authorization": "Bearer " + token})

    store = WorkspaceStore(database)
    service = WorkspaceService(store)
    return SimpleNamespace(database=database, actor=actor, owner=actor(), other=actor(),
                           store=store, service=service)


@pytest.fixture
def workspace_http(workspace_data, postgres):
    d = workspace_data
    stream = StringIO()
    app = create_app(postgres.settings(), database_factory=lambda _: d.database,
                     google_verifier_factory=Mock(), log_stream=stream)
    with TestClient(app) as client:
        yield client, d, stream


def create(d, actor=None, key=None, name="Team"):
    return d.service.create((actor or d.owner).token, name, key or uuid4())


def join(d, identifier, actor, *, revoked=False):
    with d.database.transaction() as session:
        now = database_now(session)
        session.execute(memberships.insert().values(
            id=uuid4(), workspace_id=UUID(identifier), user_id=actor.id, role="member", joined_at=now,
            revoked_at=now if revoked else None,
        ))


def rows(d, table):
    with d.database.transaction() as session:
        return session.execute(select(table)).mappings().all()


def error(status, code, operation, *args):
    with pytest.raises(APIError) as caught:
        operation(*args)
    assert (caught.value.status_code, caught.value.code) == (status, code)


def test_create_owner_membership_quota_and_current_user_are_atomic(workspace_http):
    client, d, _ = workspace_http
    key = uuid4()
    response = client.post(BASE, headers={**d.owner.headers, "Idempotency-Key": str(key)}, json={"name": " \tMy Team\n "})
    assert response.status_code == 201 and response.headers["etag"] == '"1"'
    public_contract(response.json(), "Workspace")
    assert response.json()["name"] == "My Team" and response.json()["member_count"] == 1
    assert response.json()["my_role"] == "owner"
    assert len(rows(d, workspaces)) == len(rows(d, memberships)) == len(rows(d, idempotency_records)) == 1
    assert rows(d, memberships)[0]["user_id"] == d.owner.id
    assert client.get("/api/v1/me", headers=d.owner.headers).json()["owned_workspace_count"] == 1
    saved = rows(d, idempotency_records)[0]
    assert saved["expires_at"] - saved["created_at"] >= timedelta(hours=24)


def test_tenth_owned_workspace_is_enforced_under_competing_requests(workspace_data):
    d = workspace_data
    for _ in range(9):
        create(d)
    # Joined workspaces do not consume this user's owned quota.
    joined, _ = create(d, d.other)
    join(d, joined["id"], d.owner)
    barrier = Barrier(2)
    keys = [uuid4(), uuid4()]

    def attempt(key):
        barrier.wait(timeout=10)
        try:
            return create(d, key=key)
        except APIError as failure:
            return failure

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, keys))
    assert sum(isinstance(result, tuple) for result in results) == 1
    failure = next(result for result in results if isinstance(result, APIError))
    assert failure.status_code == 409 and failure.code in {"WORKSPACE_LIMIT_REACHED", "IDEMPOTENCY_IN_PROGRESS"}
    losing_key = keys[results.index(failure)]
    error(409, "WORKSPACE_LIMIT_REACHED", create, d, None, losing_key)
    owned = [w for w in rows(d, workspaces) if w["owner_id"] == d.owner.id and w["deleted_at"] is None]
    assert len(owned) == 10
    assert next(u for u in rows(d, users) if u["id"] == d.owner.id)["owned_workspace_count"] == 10
    assert not any(record["idempotency_key"] == losing_key for record in rows(d, idempotency_records))
    # Deletion immediately frees a slot, including retry of a rejected create key.
    d.service.delete(d.owner.token, owned[0]["id"], ['"1"'])
    create(d, key=losing_key)
    assert next(u for u in rows(d, users) if u["id"] == d.owner.id)["owned_workspace_count"] == 10


def test_concurrent_identical_retries_create_exactly_one_workspace(workspace_data):
    d = workspace_data
    key, barrier = uuid4(), Barrier(2)

    def attempt(_):
        barrier.wait(timeout=10)
        return create(d, key=key)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert results[0] == results[1]
    assert len(rows(d, workspaces)) == len(rows(d, memberships)) == len(rows(d, idempotency_records)) == 1


def test_idempotency_replay_preserves_original_response_etag_and_checks_access_first(workspace_data):
    d = workspace_data
    key = uuid4()
    original = create(d, key=key)
    identifier = UUID(original[0]["id"])
    join(d, str(identifier), d.other)
    renamed = d.service.rename(d.owner.token, identifier, "New name", ['"1"'])
    assert renamed[0]["member_count"] == 2 and renamed[1] == '"2"'
    assert create(d, key=key) == original
    error(409, "IDEMPOTENCY_CONFLICT", create, d, None, key, "Different")
    # Same key is scoped independently to the other actor.
    assert create(d, d.other, key)[0]["id"] != str(identifier)
    d.service.delete(d.owner.token, identifier, ['"2"'])
    error(404, "NOT_FOUND", create, d, None, key)
    error(404, "NOT_FOUND", create, d, None, key, "Different")


def test_expired_key_can_create_a_new_resource(workspace_data):
    d = workspace_data
    key = uuid4()
    original, _ = create(d, key=key)
    with d.database.transaction() as session:
        now = database_now(session)
        session.execute(update(idempotency_records).values(
            created_at=now - timedelta(hours=26), expires_at=now - timedelta(hours=1),
        ))
    replacement, _ = create(d, key=key)
    assert original["id"] != replacement["id"] and len(rows(d, idempotency_records)) == 1


def test_failed_owner_membership_rolls_back_resource_counter_and_retry_key(workspace_data):
    d = workspace_data
    key = uuid4()

    def fail(connection, cursor, statement, parameters, context, many):
        if "INSERT INTO identity_workspace.memberships" in statement:
            raise SQLAlchemyError("synthetic-membership-write-failure")

    event.listen(d.database.engine, "before_cursor_execute", fail)
    try:
        error(503, "DEPENDENCY_UNAVAILABLE", create, d, None, key)
    finally:
        event.remove(d.database.engine, "before_cursor_execute", fail)
    assert rows(d, workspaces) == rows(d, memberships) == rows(d, idempotency_records) == []
    assert all(u["owned_workspace_count"] == 0 for u in rows(d, users))
    create(d, key=key)


def test_list_orders_ties_filters_active_memberships_and_authenticates_cursor(workspace_data):
    d = workspace_data
    owned = [create(d)[0] for _ in range(3)]
    joined, _ = create(d, d.other)
    revoked, _ = create(d, d.other)
    join(d, joined["id"], d.owner)
    join(d, revoked["id"], d.owner, revoked=True)
    with d.database.transaction() as session:
        session.execute(update(workspaces).values(created_at=database_now(session)))
    expected = sorted([w["id"] for w in [*owned, joined]], reverse=True)
    first = d.service.list(d.owner.token, 1)
    assert first["items"][0]["id"] == expected[0]
    cursor = first["next_cursor"]
    second = d.service.list(d.owner.token, 100, cursor)
    assert [w["id"] for w in second["items"]] == expected[1:]
    assert second["next_cursor"] is None
    public_contract(second, "WorkspacePage")
    assert any(w["my_role"] == "member" and w["member_count"] == 2 for w in [*first["items"], *second["items"]])
    error(400, "INVALID_CURSOR", d.service.list, d.other.token, 1, cursor)
    forged = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    error(400, "INVALID_CURSOR", d.service.list, d.owner.token, 1, forged)
    for malformed in ("", "x", "x" * 1025, "!" * 43):
        error(400, "INVALID_CURSOR", d.service.list, d.owner.token, 1, malformed)
    with d.database.transaction() as session:
        session.execute(update(workspace_cursors).values(expires_at=database_now(session) - timedelta(seconds=1)))
    error(400, "INVALID_CURSOR", d.service.list, d.owner.token, 1, cursor)


def test_page_rechecks_membership_after_cursor_issuance(workspace_data):
    d = workspace_data
    created = [create(d, d.other)[0] for _ in range(3)]
    for body in created:
        join(d, body["id"], d.owner)
    first = d.service.list(d.owner.token, 1)
    with d.database.transaction() as session:
        session.execute(update(memberships).where(memberships.c.user_id == d.owner.id).values(revoked_at=database_now(session)))
    assert d.service.list(d.owner.token, 100, first["next_cursor"]) == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("operation", ["read", "rename", "delete"])
def test_inaccessible_is_404_before_owner_and_version_checks(workspace_data, operation):
    d = workspace_data
    body, _ = create(d)
    args = {"read": (), "rename": ("Changed", []), "delete": ([],)}[operation]
    error(404, "NOT_FOUND", getattr(d.service, operation), d.other.token, UUID(body["id"]), *args)
    join(d, body["id"], d.other)
    if operation == "read":
        assert d.service.read(d.other.token, UUID(body["id"]))[0]["my_role"] == "member"
    else:
        error(403, "FORBIDDEN", getattr(d.service, operation), d.other.token, UUID(body["id"]), *args)


@pytest.mark.parametrize("operation", ["rename", "delete"])
@pytest.mark.parametrize("matches,status,code", [
    ([], 428, "PRECONDITION_REQUIRED"), (['"2"'], 412, "VERSION_CONFLICT"),
    (['W/"1"'], 400, "MALFORMED_REQUEST"), (['*'], 400, "MALFORMED_REQUEST"),
    (['1'], 400, "MALFORMED_REQUEST"), (['"1"', '"1"'], 400, "MALFORMED_REQUEST"),
])
def test_mutation_preconditions_preserve_state(workspace_data, operation, matches, status, code):
    d = workspace_data
    body, _ = create(d)
    identifier = UUID(body["id"])
    args = ("Changed", matches) if operation == "rename" else (matches,)
    error(status, code, getattr(d.service, operation), d.owner.token, identifier, *args)
    assert d.service.read(d.owner.token, identifier) == (body, '"1"')
    assert rows(d, outbox) == []


def test_noop_rename_preserves_version_timestamp_and_produces_no_event(workspace_data):
    d = workspace_data
    body, etag = create(d)
    assert d.service.rename(d.owner.token, UUID(body["id"]), "Team", [etag]) == (body, etag)
    assert rows(d, outbox) == []


def test_concurrent_stale_renames_allow_one_write(workspace_data):
    d = workspace_data
    body, _ = create(d)
    identifier, barrier = UUID(body["id"]), Barrier(2)

    def rename(name):
        barrier.wait(timeout=10)
        try:
            return d.service.rename(d.owner.token, identifier, name, ['"1"'])
        except APIError as failure:
            return failure

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(rename, ["First", "Second"]))
    assert sum(isinstance(r, tuple) for r in results) == 1
    failure = next(r for r in results if isinstance(r, APIError))
    assert failure.status_code == 412
    assert d.service.read(d.owner.token, identifier)[1] == '"2"'


def invitation_fixtures(d, identifier):
    with d.database.transaction() as session:
        now = database_now(session)
        for state in ("pending", "accepted", "declined"):
            session.execute(invitations.insert().values(
                id=uuid4(), workspace_id=identifier, inviter_id=d.owner.id,
                recipient_id=d.other.id, status=state, created_at=now, updated_at=now,
            ))


def test_delete_revokes_cancels_frees_quota_and_persists_private_safe_event_without_task_or_broker(workspace_http):
    client, d, stream = workspace_http
    key = uuid4()
    body, _ = create(d, key=key)
    identifier = UUID(body["id"])
    join(d, body["id"], d.other)
    invitation_fixtures(d, identifier)
    request_id = str(uuid4())
    response = client.delete(f"{BASE}/{identifier}", headers={**d.owner.headers, "If-Match": '"1"', "X-Request-ID": request_id})
    assert response.status_code == 204 and response.content == b""
    assert rows(d, workspaces)[0]["deleted_at"] is not None
    assert all(m["revoked_at"] is not None for m in rows(d, memberships))
    assert sorted(i["status"] for i in rows(d, invitations)) == ["accepted", "cancelled", "declined"]
    assert next(u for u in rows(d, users) if u["id"] == d.owner.id)["owned_workspace_count"] == 0
    for actor in (d.owner, d.other):
        assert client.get(f"{BASE}/{identifier}", headers=actor.headers).status_code == 404
        assert client.get(BASE, headers=actor.headers).json()["items"] == []
    assert client.delete(f"{BASE}/{identifier}", headers={**d.owner.headers, "If-Match": '"1"'}).status_code == 404
    assert client.post(BASE, headers={**d.owner.headers, "Idempotency-Key": str(key)}, json={"name": "Team"}).status_code == 404
    saved, = rows(d, outbox)
    event_contract(saved["payload"])
    assert saved["published_at"] is None and saved["attempts"] == 0
    assert str(saved["event_id"]) == saved["payload"]["id"]
    assert saved["payload"]["aggregate_version"] == 2
    assert saved["payload"]["request_id"] == request_id
    assert saved["payload"]["data"]["workspace_id"] == str(identifier)
    assert d.owner.token not in stream.getvalue()
    assert client.get("/health/ready").status_code == 200
    # Late invitation or membership writes cannot resurrect deleted access.
    with pytest.raises(IntegrityError):
        join(d, str(identifier), d.actor())
    with pytest.raises(IntegrityError):
        invitation_fixtures(d, identifier)


@pytest.mark.parametrize("failed_write", ["UPDATE identity_workspace.invitations", "INSERT INTO identity_workspace.outbox"])
def test_delete_failure_rolls_back_every_authoritative_change(workspace_data, failed_write):
    d = workspace_data
    body, _ = create(d)
    identifier = UUID(body["id"])
    join(d, body["id"], d.other)
    invitation_fixtures(d, identifier)
    before = {table.name: rows(d, table) for table in (workspaces, memberships, invitations, users)}

    def fail(connection, cursor, statement, parameters, context, many):
        if failed_write in statement:
            raise SQLAlchemyError("synthetic-private-deletion-failure")

    event.listen(d.database.engine, "before_cursor_execute", fail)
    try:
        error(503, "DEPENDENCY_UNAVAILABLE", d.service.delete, d.owner.token, identifier, ['"1"'])
    finally:
        event.remove(d.database.engine, "before_cursor_execute", fail)
    for table in (workspaces, memberships, invitations, users):
        assert rows(d, table) == before[table.name]
    assert rows(d, outbox) == []
    d.service.delete(d.owner.token, identifier, ['"1"'])


def test_concurrent_deletes_emit_one_event_and_release_quota_once(workspace_data):
    d = workspace_data
    body, _ = create(d)
    identifier, barrier = UUID(body["id"]), Barrier(2)

    def remove(_):
        barrier.wait(timeout=10)
        try:
            d.service.delete(d.owner.token, identifier, ['"1"'])
            return 204
        except APIError as failure:
            return failure.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(remove, range(2))) == [204, 404]
    assert len(rows(d, outbox)) == 1
    assert all(u["owned_workspace_count"] == 0 for u in rows(d, users))


@pytest.mark.parametrize("operation", ["list", "create", "read", "rename", "delete"])
@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_real_session_expiry_and_logout_block_every_workspace_operation(workspace_data, operation, state):
    d = workspace_data
    body, _ = create(d)
    with d.database.transaction() as session:
        now = database_now(session)
        values = {"revoked_at": now} if state == "revoked" else {
            "created_at": now - timedelta(hours=9), "expires_at": now - timedelta(hours=1),
        }
        session.execute(update(sessions).where(sessions.c.user_id == d.owner.id).values(**values))
    args = {
        "list": (), "create": ("Team", uuid4()), "read": (UUID(body["id"]),),
        "rename": (UUID(body["id"]), "Changed", ['"1"']), "delete": (UUID(body["id"]), ['"1"']),
    }[operation]
    error(401, "SESSION_INVALID", getattr(d.service, operation), d.owner.token, *args)
    assert len(rows(d, workspaces)) == 1 and rows(d, outbox) == []


def test_outbox_payload_immutable_but_publishing_bookkeeping_can_change(workspace_data):
    d = workspace_data
    body, _ = create(d)
    d.service.delete(d.owner.token, UUID(body["id"]), ['"1"'])
    changed = {**rows(d, outbox)[0]["payload"], "aggregate_version": 99}
    with pytest.raises(IntegrityError):
        with d.database.transaction() as session:
            session.execute(update(outbox).values(payload=changed))
    with d.database.transaction() as session:
        session.execute(update(outbox).values(attempts=1, next_attempt_at=database_now(session)))
    assert rows(d, outbox)[0]["attempts"] == 1


def test_cleanup_only_removes_expired_retry_and_cursor_rows(workspace_data):
    d = workspace_data
    first, _ = create(d)
    create(d)
    d.service.list(d.owner.token, 1)
    d.service.delete(d.owner.token, UUID(first["id"]), ['"1"'])
    assert d.store.cleanup() == {"idempotency_records": 0, "workspace_cursors": 0}
    with d.database.transaction() as session:
        now = database_now(session)
        session.execute(update(idempotency_records).values(created_at=now - timedelta(hours=26), expires_at=now - timedelta(hours=1)))
        session.execute(update(workspace_cursors).values(expires_at=now - timedelta(hours=1)))
    counts = d.store.cleanup(limit=1)
    assert counts == {"idempotency_records": 1, "workspace_cursors": 1}
    assert len(rows(d, workspaces)) == 2 and len(rows(d, outbox)) == 1
    assert len(rows(d, memberships)) == 2


def test_workspace_migration_upgrades_existing_users_and_round_trips(database, migrate):
    migrate("downgrade", "0002_google_sessions")
    identifier = uuid4()
    with database.transaction() as session:
        session.execute(users.insert().values(id=identifier, provider="google", provider_subject="migration-fixture",
                                             display_name="Existing User", email="existing@example.com", created_at=database_now(session)))
    migrate()
    migrate("check")
    with database.transaction() as session:
        assert session.scalar(select(users.c.display_name).where(users.c.id == identifier)) == "Existing User"
    assert database.check_readiness()
