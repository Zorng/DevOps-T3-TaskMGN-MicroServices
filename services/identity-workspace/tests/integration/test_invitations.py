"""Invitation permissions, state transitions and concurrency on disposable PostgreSQL."""
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
from identity_workspace.invitations.models import InviteCreate
from identity_workspace.invitations.service import InvitationService
from identity_workspace.invitations.store import InvitationStore
from identity_workspace.main import create_app
from identity_workspace.memberships.primitives import join_member
from identity_workspace.memberships.service import MembershipService
from identity_workspace.memberships.store import MembershipStore
from identity_workspace.persistence import idempotency_records, invitation_cursors, invitations, memberships, outbox, sessions, users
from identity_workspace.workspaces.service import WorkspaceService
from identity_workspace.workspaces.store import WorkspaceStore
from tests.workspace_contract import event_contract, public_contract


@pytest.fixture
def invitation_data(database, registered_actor):
    def actor(name="Registered Recipient", email=None):
        email = email or f"{uuid4()}@example.com"
        value = registered_actor(name, email)
        value.email = email
        return value
    owner, recipient, member, outsider = actor("Owner"), actor("Recipient", "private.recipient@example.com"), actor("Member"), actor("Outsider")
    workspace_service = WorkspaceService(WorkspaceStore(database))
    body, _ = workspace_service.create(owner.token, "Invitation Workspace", uuid4())
    workspace_id = UUID(body["id"])
    def join(value):
        with database.transaction() as session:
            session_actor(session, token_hash(value.token), lock=True)
            return join_member(session, workspace_id, value.id)
    join(member)
    store = InvitationStore(database)
    return SimpleNamespace(database=database, actor=actor, owner=owner, recipient=recipient, member=member,
                           outsider=outsider, workspace_id=workspace_id, join=join, store=store,
                           service=InvitationService(store), workspace_service=workspace_service,
                           membership_service=MembershipService(MembershipStore(database)))


@pytest.fixture
def invitation_http(invitation_data, postgres):
    d = invitation_data
    stream = StringIO()
    app = create_app(postgres.settings(), database_factory=lambda _: d.database,
                     google_verifier_factory=Mock(), log_stream=stream)
    with TestClient(app) as client:
        yield client, d, stream


def create(d, recipient=None, *, key=None, email=None, owner=None, workspace_id=None):
    return d.service.create((owner or d.owner).token, workspace_id or d.workspace_id,
                            InviteCreate(email=email or (recipient or d.recipient).email).email, key or uuid4())


def rows(d, table):
    with d.database.transaction() as session:
        return session.execute(select(table)).mappings().all()


def error(status, code, operation, *args, **kwargs):
    with pytest.raises(APIError) as caught:
        operation(*args, **kwargs)
    assert (caught.value.status_code, caught.value.code) == (status, code)


def test_create_lookup_normalization_event_and_retry_record_commit_together(invitation_http):
    client, d, stream = invitation_http
    key, request_id = str(uuid4()), str(uuid4())
    before = d.workspace_service.read(d.owner.token, d.workspace_id)
    response = client.post(f"/api/v1/workspaces/{d.workspace_id}/invitations", headers={
        **d.owner.headers, "Idempotency-Key": key, "X-Request-ID": request_id,
    }, json={"email": " \tPRIVATE.RECIPIENT@EXAMPLE.COM\n "})
    assert response.status_code == 201 and "etag" not in response.headers
    public_contract(response.json(), "Invitation")
    assert response.json()["recipient"]["id"] == str(d.recipient.id)
    assert d.workspace_service.read(d.owner.token, d.workspace_id) == before
    saved, = rows(d, outbox)
    event_contract(saved["payload"])
    assert saved["payload"]["aggregate_id"] == response.json()["id"]
    assert saved["payload"]["aggregate_version"] == 1
    assert saved["payload"]["request_id"] == request_id
    assert saved["published_at"] is None
    record = next(r for r in rows(d, idempotency_records) if r["idempotency_key"] == UUID(key))
    assert record["status_code"] == 201 and record["etag"] is None
    assert record["expires_at"] - record["created_at"] >= timedelta(hours=24)
    assert record["response_body"] == response.json()
    assert d.recipient.email not in response.text + stream.getvalue() + json.dumps(saved["payload"])


@pytest.mark.parametrize("case,status,code", [("missing", 404, "USER_NOT_FOUND"), ("ambiguous", 409, "EMAIL_AMBIGUOUS"), ("member", 409, "ALREADY_MEMBER"), ("owner", 409, "ALREADY_MEMBER")])
def test_owner_only_lookup_errors_never_write_partial_invitation(invitation_data, case, status, code):
    d = invitation_data
    key = uuid4()
    if case == "ambiguous":
        d.actor(email=d.recipient.email.upper())
    email = {"missing": "unregistered@example.com", "ambiguous": d.recipient.email, "member": d.member.email, "owner": d.owner.email}[case]
    error(status, code, create, d, key=key, email=email)
    assert rows(d, invitations) == rows(d, outbox) == []
    assert not any(r["idempotency_key"] == key for r in rows(d, idempotency_records))
    if case == "missing":
        d.actor(email=email)
        create(d, key=key, email=email)


def test_lookup_preserves_plus_aliases_and_dots_as_distinct_registered_users(invitation_data):
    d = invitation_data
    recipients = [d.actor(email=email) for email in ("person@example.com", "per.son@example.com", "person+tag@example.com")]
    results = [create(d, recipient=recipient) for recipient in recipients]
    assert {result["recipient"]["id"] for result in results} == {str(r.id) for r in recipients}


@pytest.mark.parametrize("operation", ["create", "list_owned"])
@pytest.mark.parametrize("role,status", [("member", 403), ("outsider", 404)])
def test_workspace_invitation_operations_check_owner_before_lookup(invitation_data, operation, role, status):
    d = invitation_data
    actor = getattr(d, role)
    args = ("unregistered@example.com", uuid4()) if operation == "create" else ()
    error(status, "FORBIDDEN" if status == 403 else "NOT_FOUND", getattr(d.service, operation), actor.token, d.workspace_id, *args)
    assert rows(d, invitations) == []


def test_detail_is_visible_only_to_recipient_or_current_owner(invitation_data):
    d = invitation_data
    body = create(d)
    identifier = UUID(body["id"])
    for actor in (d.owner, d.recipient):
        assert d.service.read(actor.token, identifier) == body
    for actor in (d.member, d.outsider):
        error(404, "NOT_FOUND", d.service.read, actor.token, identifier)


@pytest.mark.parametrize("state", ["pending", "accepted", "declined", "cancelled"])
def test_permissions_precede_terminal_state_conflicts(invitation_data, state):
    d = invitation_data
    body = create(d)
    identifier = UUID(body["id"])
    if state in {"accepted", "declined"}:
        d.service.respond(d.recipient.token, identifier, "accept" if state == "accepted" else "decline")
    elif state == "cancelled":
        d.service.cancel(d.owner.token, identifier)
    error(403, "FORBIDDEN", d.service.respond, d.owner.token, identifier, "accept")
    error(403, "FORBIDDEN", d.service.cancel, d.recipient.token, identifier)
    for actor in (d.member, d.outsider):
        error(404, "NOT_FOUND", d.service.respond, actor.token, identifier, "accept")
        error(404, "NOT_FOUND", d.service.cancel, actor.token, identifier)


@pytest.mark.parametrize("current", ["pending", "accepted", "declined", "cancelled"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_terminal_actions_are_state_idempotent_and_conflicting_actions_fail(invitation_data, current, action):
    d = invitation_data
    identifier = UUID(create(d)["id"])
    original = d.service.read(d.owner.token, identifier)
    if current in {"accepted", "declined"}:
        original = d.service.respond(d.recipient.token, identifier, "accept" if current == "accepted" else "decline")
    elif current == "cancelled":
        original = d.service.cancel(d.owner.token, identifier)
    desired = {"accept": "accepted", "decline": "declined", "cancel": "cancelled"}[action]
    operation = lambda: d.service.cancel(d.owner.token, identifier) if action == "cancel" else d.service.respond(d.recipient.token, identifier, action)
    before = d.workspace_service.read(d.owner.token, d.workspace_id)
    if current != "pending" and current != desired:
        error(409, "INVITATION_NOT_PENDING", operation)
        assert d.workspace_service.read(d.owner.token, d.workspace_id) == before
    else:
        result = operation()
        assert result["status"] == desired
        if current == desired:
            assert result == original and d.workspace_service.read(d.owner.token, d.workspace_id) == before
    assert len(rows(d, memberships)) == 2 + int(current == "accepted" or (current == "pending" and action == "accept"))
    assert len(rows(d, outbox)) == 1


def test_replaying_accepted_invitation_after_leaving_never_rejoins(invitation_data):
    d = invitation_data
    identifier = UUID(create(d)["id"])
    accepted = d.service.respond(d.recipient.token, identifier, "accept")
    old_generation = next(m["id"] for m in rows(d, memberships) if m["user_id"] == d.recipient.id)
    d.membership_service.leave(d.recipient.token, d.workspace_id)
    before = d.workspace_service.read(d.owner.token, d.workspace_id)
    assert d.service.respond(d.recipient.token, identifier, "accept") == accepted
    assert d.workspace_service.read(d.owner.token, d.workspace_id) == before
    assert all(m["revoked_at"] is not None for m in rows(d, memberships) if m["user_id"] == d.recipient.id)
    fresh = UUID(create(d)["id"])
    d.service.respond(d.recipient.token, fresh, "accept")
    replacement = next(m["id"] for m in rows(d, memberships) if m["user_id"] == d.recipient.id and m["revoked_at"] is None)
    assert replacement != old_generation


def test_pending_invitations_do_not_reserve_capacity_and_full_accept_stays_pending(invitation_data):
    d = invitation_data
    for _ in range(18):
        d.join(d.actor())
    before = d.workspace_service.read(d.owner.token, d.workspace_id)
    bodies = [create(d, recipient=recipient) for recipient in (d.recipient, d.outsider)]
    for actor, body in zip((d.recipient, d.outsider), bodies):
        error(409, "MEMBER_LIMIT_REACHED", d.service.respond, actor.token, UUID(body["id"]), "accept")
        assert d.service.read(actor.token, UUID(body["id"])) == body
    assert d.workspace_service.read(d.owner.token, d.workspace_id) == before
    assert len(rows(d, invitations)) == 2 and len(rows(d, memberships)) == 20


def test_pending_invitations_never_expire(invitation_data):
    d = invitation_data
    identifier = uuid4()
    with d.database.transaction() as session:
        old = database_now(session) - timedelta(days=365)
        session.execute(invitations.insert().values(id=identifier, workspace_id=d.workspace_id,
                        inviter_id=d.owner.id, recipient_id=d.recipient.id, status="pending", created_at=old, updated_at=old))
    assert d.service.read(d.recipient.token, identifier)["status"] == "pending"
    assert d.service.respond(d.recipient.token, identifier, "accept")["status"] == "accepted"


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_creation_serializes_duplicate_invitation_and_event(invitation_data, same_key):
    d = invitation_data
    first_key, barrier = uuid4(), Barrier(2)
    keys = [first_key, first_key if same_key else uuid4()]
    def attempt(key):
        barrier.wait(timeout=10)
        try:
            return create(d, key=key)
        except APIError as failure:
            return failure
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, keys))
    if same_key:
        assert results[0] == results[1]
    else:
        failure = next(r for r in results if isinstance(r, APIError))
        assert failure.code == "INVITATION_ALREADY_PENDING"
    assert len(rows(d, invitations)) == len(rows(d, outbox)) == 1


def test_concurrent_accepts_of_one_invitation_create_one_generation(invitation_data):
    d = invitation_data
    identifier, barrier = UUID(create(d)["id"]), Barrier(2)
    before, _ = d.workspace_service.read(d.owner.token, d.workspace_id)
    def accept(_):
        barrier.wait(timeout=10)
        return d.service.respond(d.recipient.token, identifier, "accept")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(accept, range(2)))
    assert results[0] == results[1]
    assert len([m for m in rows(d, memberships) if m["user_id"] == d.recipient.id]) == 1
    assert d.workspace_service.read(d.owner.token, d.workspace_id)[0]["version"] == before["version"] + 1


def test_concurrent_accepts_for_twentieth_slot_leave_loser_pending(invitation_data):
    d = invitation_data
    for _ in range(17):
        d.join(d.actor())
    recipients = [d.recipient, d.outsider]
    ids = [UUID(create(d, recipient=actor)["id"]) for actor in recipients]
    barrier = Barrier(2)
    def accept(index):
        barrier.wait(timeout=10)
        try:
            return d.service.respond(recipients[index].token, ids[index], "accept")
        except APIError as failure:
            return failure
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(accept, range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    failure = next(r for r in results if isinstance(r, APIError))
    assert failure.code == "MEMBER_LIMIT_REACHED"
    assert sorted(i["status"] for i in rows(d, invitations)) == ["accepted", "pending"]
    assert len([m for m in rows(d, memberships) if m["revoked_at"] is None]) == 20


@pytest.mark.parametrize("actions", [("accept", "cancel"), ("accept", "decline"), ("cancel", "cancel"), ("decline", "decline")])
def test_competing_terminal_actions_have_one_consistent_outcome(invitation_data, actions):
    d = invitation_data
    identifier, barrier = UUID(create(d)["id"]), Barrier(2)
    def attempt(action):
        barrier.wait(timeout=10)
        try:
            return d.service.cancel(d.owner.token, identifier) if action == "cancel" else d.service.respond(d.recipient.token, identifier, action)
        except APIError as failure:
            return failure
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, actions))
    if actions[0] == actions[1]:
        assert results[0] == results[1]
    else:
        assert sum(isinstance(r, dict) for r in results) == 1
        assert next(r for r in results if isinstance(r, APIError)).code == "INVITATION_NOT_PENDING"
    state = rows(d, invitations)[0]["status"]
    assert len(rows(d, memberships)) == 2 + int(state == "accepted")


@pytest.mark.parametrize("failed_write", ["INSERT INTO identity_workspace.invitations", "INSERT INTO identity_workspace.outbox", "INSERT INTO identity_workspace.idempotency_records"])
def test_creation_rolls_back_invitation_event_and_key_on_failure(invitation_data, failed_write):
    d, key = invitation_data, uuid4()
    def fail(connection, cursor, statement, parameters, context, many):
        if failed_write in statement:
            raise SQLAlchemyError("synthetic-private-invitation-write-failure")
    event.listen(d.database.engine, "before_cursor_execute", fail)
    try:
        error(503, "DEPENDENCY_UNAVAILABLE", create, d, key=key)
    finally:
        event.remove(d.database.engine, "before_cursor_execute", fail)
    assert rows(d, invitations) == rows(d, outbox) == []
    assert not any(r["idempotency_key"] == key for r in rows(d, idempotency_records))
    create(d, key=key)


@pytest.mark.parametrize("failed_write", ["INSERT INTO identity_workspace.memberships", "UPDATE identity_workspace.workspaces", "UPDATE identity_workspace.invitations"])
def test_acceptance_rolls_back_join_version_and_state_on_failure(invitation_data, failed_write):
    d = invitation_data
    identifier = UUID(create(d)["id"])
    before = d.workspace_service.read(d.owner.token, d.workspace_id)
    def fail(connection, cursor, statement, parameters, context, many):
        if failed_write in statement:
            raise SQLAlchemyError("synthetic-private-acceptance-failure")
    event.listen(d.database.engine, "before_cursor_execute", fail)
    try:
        error(503, "DEPENDENCY_UNAVAILABLE", d.service.respond, d.recipient.token, identifier, "accept")
    finally:
        event.remove(d.database.engine, "before_cursor_execute", fail)
    assert d.service.read(d.recipient.token, identifier)["status"] == "pending"
    assert d.workspace_service.read(d.owner.token, d.workspace_id) == before
    assert len(rows(d, memberships)) == 2 and len(rows(d, outbox)) == 1
    d.service.respond(d.recipient.token, identifier, "accept")


def test_deletion_cancels_pending_and_hides_all_invitation_operations_and_replays(invitation_data):
    d, key = invitation_data, uuid4()
    bodies = [create(d, recipient=actor, key=key if i == 0 else None) for i, actor in enumerate((d.recipient, d.outsider, d.actor(), d.actor()))]
    ids = [UUID(body["id"]) for body in bodies]
    actors = [d.recipient, d.outsider]
    d.service.respond(d.outsider.token, ids[1], "accept")
    with d.database.transaction() as session:
        terminal_recipient = session.scalar(select(invitations.c.recipient_id).where(invitations.c.id == ids[2]))
        session.execute(update(invitations).where(invitations.c.id == ids[2]).values(status="declined"))
    d.service.cancel(d.owner.token, ids[3])
    _, etag = d.workspace_service.read(d.owner.token, d.workspace_id)
    d.workspace_service.delete(d.owner.token, d.workspace_id, [etag])
    assert sorted(i["status"] for i in rows(d, invitations)) == ["accepted", "cancelled", "cancelled", "declined"]
    for identifier in ids:
        error(404, "NOT_FOUND", d.service.read, d.owner.token, identifier)
        error(404, "NOT_FOUND", d.service.cancel, d.owner.token, identifier)
    error(404, "NOT_FOUND", d.service.respond, d.recipient.token, ids[0], "accept")
    error(404, "NOT_FOUND", d.service.list_owned, d.owner.token, d.workspace_id)
    error(404, "NOT_FOUND", create, d, key=key)
    error(404, "NOT_FOUND", create, d, key=key, email="different@example.com")
    assert d.service.list_received(d.recipient.token)["items"] == []


def test_idempotent_create_replays_historical_body_after_response_and_rename(invitation_data):
    d, key = invitation_data, uuid4()
    original = create(d, key=key)
    identifier = UUID(original["id"])
    d.service.respond(d.recipient.token, identifier, "accept")
    _, etag = d.workspace_service.read(d.owner.token, d.workspace_id)
    d.workspace_service.rename(d.owner.token, d.workspace_id, "Renamed", [etag])
    assert create(d, key=key) == original
    current = d.service.read(d.recipient.token, identifier)
    assert current["status"] == "accepted" and current["workspace_name"] == "Renamed"
    error(409, "IDEMPOTENCY_CONFLICT", create, d, key=key, recipient=d.outsider)
    second, _ = d.workspace_service.create(d.owner.token, "Other", uuid4())
    assert create(d, key=key, workspace_id=UUID(second["id"]))["id"] != original["id"]


def test_expired_create_key_can_be_reused_after_terminal_invitation(invitation_data):
    d, key = invitation_data, uuid4()
    original = create(d, key=key)
    d.service.cancel(d.owner.token, UUID(original["id"]))
    with d.database.transaction() as session:
        now = database_now(session)
        session.execute(update(idempotency_records).where(idempotency_records.c.idempotency_key == key).values(
            created_at=now - timedelta(hours=26), expires_at=now - timedelta(hours=1)))
    assert create(d, key=key)["id"] != original["id"]


def test_owner_pagination_orders_ties_and_binds_cursor_to_filter_parent_actor_and_scope(invitation_data, monkeypatch):
    d = invitation_data
    with d.database.transaction() as session:
        fixed = database_now(session)
    monkeypatch.setattr("identity_workspace.invitations.store.database_now", lambda _: fixed)
    bodies = [create(d, recipient=actor) for actor in (d.recipient, d.outsider, d.actor(), d.actor())]
    expected = sorted([b["id"] for b in bodies], reverse=True)
    first = d.service.list_owned(d.owner.token, d.workspace_id, 1)
    assert first["items"][0]["id"] == expected[0]
    cursor = first["next_cursor"]
    second = d.service.list_owned(d.owner.token, d.workspace_id, 100, cursor)
    public_contract(second, "InvitationPage")
    assert [i["id"] for i in second["items"]] == expected[1:] and second["next_cursor"] is None
    error(400, "INVALID_CURSOR", d.service.list_owned, d.owner.token, d.workspace_id, 1, cursor, "pending")
    other, _ = d.workspace_service.create(d.owner.token, "Other", uuid4())
    error(400, "INVALID_CURSOR", d.service.list_owned, d.owner.token, UUID(other["id"]), 1, cursor)
    error(400, "INVALID_CURSOR", d.service.list_received, d.owner.token, 1, cursor)
    for forged in ("", "!" * 43, ("A" if cursor[0] != "A" else "B") + cursor[1:]):
        error(400, "INVALID_CURSOR", d.service.list_owned, d.owner.token, d.workspace_id, 1, forged)
    with d.database.transaction() as session:
        session.execute(update(invitation_cursors).values(expires_at=fixed - timedelta(seconds=1)))
    error(400, "INVALID_CURSOR", d.service.list_owned, d.owner.token, d.workspace_id, 1, cursor)
    assert d.store.cleanup(limit=1) == 1
    assert len(rows(d, invitations)) == 4


def test_recipient_lists_filters_visibility_cursor_scope_and_deleted_parents(invitation_data):
    d = invitation_data
    first = create(d)
    second_ws, _ = d.workspace_service.create(d.owner.token, "Second", uuid4())
    second = create(d, workspace_id=UUID(second_ws["id"]))
    third_ws, _ = d.workspace_service.create(d.owner.token, "Third", uuid4())
    third = create(d, workspace_id=UUID(third_ws["id"]))
    d.service.respond(d.recipient.token, UUID(first["id"]), "accept")
    assert len(d.service.list_received(d.recipient.token, 100, None, "pending")["items"]) == 2
    assert [i["id"] for i in d.service.list_received(d.recipient.token, 100, None, "accepted")["items"]] == [first["id"]]
    page = d.service.list_received(d.recipient.token, 1)
    cursor = page["next_cursor"]
    error(400, "INVALID_CURSOR", d.service.list_received, d.outsider.token, 1, cursor)
    error(400, "INVALID_CURSOR", d.service.list_received, d.recipient.token, 1, cursor, "pending")
    _, etag = d.workspace_service.read(d.owner.token, UUID(third_ws["id"]))
    d.workspace_service.delete(d.owner.token, UUID(third_ws["id"]), [etag])
    assert {i["id"] for i in d.service.list_received(d.recipient.token, 100)["items"]} == {first["id"], second["id"]}
    assert d.service.list_received(d.outsider.token)["items"] == []


@pytest.mark.parametrize("operation", ["create", "list_owned", "list_received", "read", "respond", "cancel"])
@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_real_session_failures_block_every_invitation_operation(invitation_data, operation, state):
    d = invitation_data
    identifier = UUID(create(d)["id"])
    owner_action = operation in {"create", "list_owned", "cancel"}
    actor = d.owner if owner_action else d.recipient
    with d.database.transaction() as session:
        now = database_now(session)
        values = {"revoked_at": now} if state == "revoked" else {"created_at": now - timedelta(hours=9), "expires_at": now - timedelta(hours=1)}
        session.execute(update(sessions).where(sessions.c.user_id == actor.id).values(**values))
    args = {"create": (d.workspace_id, d.outsider.email, uuid4()), "list_owned": (d.workspace_id,),
            "list_received": (), "read": (identifier,), "respond": (identifier, "accept"), "cancel": (identifier,)}[operation]
    error(401, "SESSION_INVALID", getattr(d.service, operation), actor.token, *args)
    assert len(rows(d, invitations)) == 1 and len(rows(d, memberships)) == 2


@pytest.mark.parametrize("terminal", ["accepted", "declined", "cancelled"])
def test_terminal_outcomes_cannot_be_reopened_or_retimed_in_storage(invitation_data, terminal):
    d = invitation_data
    identifier = UUID(create(d)["id"])
    if terminal == "cancelled":
        d.service.cancel(d.owner.token, identifier)
    else:
        d.service.respond(d.recipient.token, identifier, "accept" if terminal == "accepted" else "decline")
    with pytest.raises(IntegrityError):
        with d.database.transaction() as session:
            session.execute(update(invitations).where(invitations.c.id == identifier).values(status="pending"))
    with pytest.raises(IntegrityError):
        with d.database.transaction() as session:
            session.execute(update(invitations).where(invitations.c.id == identifier).values(updated_at=database_now(session) + timedelta(days=1)))


def test_migration_preserves_invitation_and_successful_retry_history(invitation_data, migrate):
    d, key = invitation_data, uuid4()
    body = create(d, key=key)
    migrate("downgrade", "0004_membership_generations")
    migrate()
    migrate("check")
    assert create(d, key=key) == body and len(rows(d, outbox)) == 1
    assert d.database.check_readiness()
