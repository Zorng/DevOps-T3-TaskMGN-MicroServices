"""Actor → workspace → invitation/member locks; one authoritative transaction."""
from datetime import timedelta
import hashlib
import json
import re
import secrets
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text, tuple_, update

from identity_workspace.auth.service import token_hash
from identity_workspace.auth.store import database_now, session_actor
from identity_workspace.errors import APIError
from identity_workspace.invitations.models import created_event, public_invitation, transition
from identity_workspace.memberships.models import require_owner
from identity_workspace.memberships.primitives import current_membership, join_member, locked_workspace, require_membership
from identity_workspace.persistence import idempotency_records, invitation_cursors, invitations, outbox, users, workspaces

RETENTION = timedelta(hours=25)


def invitation_query():
    inviter, recipient = users.alias("inviter"), users.alias("recipient")
    return select(
        invitations, workspaces.c.name.label("workspace_name"),
        inviter.c.display_name.label("inviter_name"), recipient.c.display_name.label("recipient_name"),
    ).join(workspaces, workspaces.c.id == invitations.c.workspace_id).join(
        inviter, inviter.c.id == invitations.c.inviter_id,
    ).join(recipient, recipient.c.id == invitations.c.recipient_id).where(workspaces.c.deleted_at.is_(None))


def fingerprint(email):
    return hashlib.sha256(json.dumps({"email": email}, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).digest()


def owner_access(session, workspace, actor):
    if workspace["owner_id"] != actor["id"]:
        return False
    mine = current_membership(session, workspace["id"], actor["id"])
    return mine is not None and mine["role"] == "owner"


class InvitationStore:
    def __init__(self, database):
        self.database = database

    def _owner_workspace(self, session, hashed_token, identifier, *, write=False):
        session_actor(session, hashed_token, lock=write)
        workspace = locked_workspace(session, identifier, write=write)
        actor = session_actor(session, hashed_token)
        mine = require_membership(session, identifier, actor["id"])
        require_owner(workspace, mine, actor["id"])
        return actor, workspace

    def _invitation(self, session, hashed_token, identifier, *, write=False):
        session_actor(session, hashed_token, lock=write)
        workspace_id = session.scalar(select(invitations.c.workspace_id).where(invitations.c.id == identifier))
        if workspace_id is None:
            raise APIError(404)
        workspace = locked_workspace(session, workspace_id, write=write)
        actor = session_actor(session, hashed_token)
        statement = invitation_query().where(invitations.c.id == identifier,
                                             invitations.c.workspace_id == workspace_id)
        if write:
            statement = statement.with_for_update(of=invitations)
        row = session.execute(statement).mappings().one_or_none()
        if row is None:
            raise APIError(404)
        owner = owner_access(session, workspace, actor)
        if row["recipient_id"] != actor["id"] and not owner:
            raise APIError(404)
        return actor, workspace, row, owner

    def _public(self, session, identifier):
        return public_invitation(session.execute(invitation_query().where(
            invitations.c.id == identifier,
        )).mappings().one())

    def create(self, hashed_token, workspace_id, email, key):
        with self.database.transaction() as session:
            session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
            actor, _ = self._owner_workspace(session, hashed_token, workspace_id, write=True)
            now = database_now(session)
            path = f"/api/v1/workspaces/{workspace_id}/invitations"
            scope = (
                (idempotency_records.c.actor_id == actor["id"])
                & (idempotency_records.c.request_method == "POST")
                & (idempotency_records.c.canonical_path == path)
                & (idempotency_records.c.idempotency_key == key)
            )
            saved = session.execute(select(idempotency_records).where(scope)).mappings().one_or_none()
            digest = fingerprint(email)
            if saved is not None and saved["expires_at"] > now:
                # Fresh permission/visibility before fingerprint conflicts or historical data.
                original = session.scalar(select(invitations.c.id).where(
                    invitations.c.id == saved["resource_id"], invitations.c.workspace_id == workspace_id,
                ))
                if original is None:
                    raise APIError(404)
                if digest != saved["fingerprint"]:
                    raise APIError(409, code="IDEMPOTENCY_CONFLICT")
                return saved["response_body"]
            if saved is not None:
                session.execute(delete(idempotency_records).where(scope))
            matches = session.scalars(select(users.c.id).where(func.lower(users.c.email) == email).limit(2)).all()
            if not matches:
                raise APIError(404, code="USER_NOT_FOUND")
            if len(matches) > 1:
                raise APIError(409, code="EMAIL_AMBIGUOUS")
            recipient_id = matches[0]
            if current_membership(session, workspace_id, recipient_id) is not None:
                raise APIError(409, code="ALREADY_MEMBER")
            pending = session.scalar(select(invitations.c.id).where(
                invitations.c.workspace_id == workspace_id, invitations.c.recipient_id == recipient_id,
                invitations.c.status == "pending",
            ))
            if pending is not None:
                raise APIError(409, code="INVITATION_ALREADY_PENDING")
            identifier = uuid4()
            session.execute(invitations.insert().values(
                id=identifier, workspace_id=workspace_id, inviter_id=actor["id"], recipient_id=recipient_id,
                status="pending", created_at=now, updated_at=now,
            ))
            event = created_event(identifier, workspace_id, recipient_id, actor, now)
            session.execute(outbox.insert().values(event_id=UUID(event["id"]), event_type=event["type"],
                                                  payload=event, created_at=now, next_attempt_at=now))
            body = self._public(session, identifier)
            saved_at = database_now(session)
            session.execute(idempotency_records.insert().values(
                actor_id=actor["id"], request_method="POST", canonical_path=path, idempotency_key=key,
                fingerprint=digest, resource_id=identifier, status_code=201, response_body=body, etag=None,
                created_at=saved_at, expires_at=saved_at + RETENTION,
            ))
            return body

    def read(self, hashed_token, identifier):
        with self.database.transaction() as session:
            _, _, row, _ = self._invitation(session, hashed_token, identifier)
            return public_invitation(row)

    def respond(self, hashed_token, identifier, action):
        with self.database.transaction() as session:
            actor, workspace, row, _ = self._invitation(session, hashed_token, identifier, write=True)
            if actor["id"] != row["recipient_id"]:
                raise APIError(403)
            requested = "accepted" if action == "accept" else "declined"
            if transition(row["status"], requested):
                if action == "accept":
                    join_member(session, workspace["id"], actor["id"])
                session.execute(update(invitations).where(invitations.c.id == identifier).values(
                    status=requested, updated_at=database_now(session),
                ))
            return self._public(session, identifier)

    def cancel(self, hashed_token, identifier):
        with self.database.transaction() as session:
            _, _, row, owner = self._invitation(session, hashed_token, identifier, write=True)
            if not owner:
                raise APIError(403)
            if transition(row["status"], "cancelled"):
                session.execute(update(invitations).where(invitations.c.id == identifier).values(
                    status="cancelled", updated_at=database_now(session),
                ))
            return self._public(session, identifier)

    def list_owned(self, hashed_token, workspace_id, limit=20, cursor=None, status=None):
        with self.database.transaction() as session:
            actor, _ = self._owner_workspace(session, hashed_token, workspace_id)
            path = f"/api/v1/workspaces/{workspace_id}/invitations"
            return self._page(session, actor, invitation_query().where(invitations.c.workspace_id == workspace_id),
                              path, limit, cursor, status)

    def list_received(self, hashed_token, limit=20, cursor=None, status=None):
        with self.database.transaction() as session:
            actor = session_actor(session, hashed_token)
            return self._page(session, actor, invitation_query().where(invitations.c.recipient_id == actor["id"]),
                              "/api/v1/invitations", limit, cursor, status)

    def _page(self, session, actor, statement, path, limit, cursor, status):
        now = database_now(session)
        scope = f"GET:{path}:status={status or '*'}:created_at-desc,id-desc:v1"
        if status is not None:
            statement = statement.where(invitations.c.status == status)
        if cursor is not None:
            if re.fullmatch(r"[A-Za-z0-9_-]{43}", cursor) is None:
                raise APIError(400, code="INVALID_CURSOR")
            position = session.execute(select(invitation_cursors).where(
                invitation_cursors.c.token_hash == token_hash(cursor), invitation_cursors.c.actor_id == actor["id"],
                invitation_cursors.c.scope == scope, invitation_cursors.c.expires_at > now,
            )).mappings().one_or_none()
            if position is None:
                raise APIError(400, code="INVALID_CURSOR")
            statement = statement.where(tuple_(invitations.c.created_at, invitations.c.id) < tuple_(
                position["last_created_at"], position["last_id"],
            ))
        rows = session.execute(statement.order_by(invitations.c.created_at.desc(), invitations.c.id.desc()).limit(
            limit + 1)).mappings().all()
        next_cursor = None
        if len(rows) > limit:
            next_cursor, last = secrets.token_urlsafe(32), rows[limit - 1]
            session.execute(invitation_cursors.insert().values(
                token_hash=token_hash(next_cursor), actor_id=actor["id"], scope=scope,
                last_created_at=last["created_at"], last_id=last["id"], created_at=now, expires_at=now + RETENTION,
            ))
        return {"items": [public_invitation(row) for row in rows[:limit]], "next_cursor": next_cursor}

    def cleanup(self, limit=1000):
        with self.database.transaction() as session:
            expired = select(invitation_cursors.c.token_hash).where(
                invitation_cursors.c.expires_at <= func.clock_timestamp(),
            ).limit(limit).with_for_update(skip_locked=True)
            return session.execute(delete(invitation_cursors).where(invitation_cursors.c.token_hash.in_(expired))).rowcount
