"""PostgreSQL transactions: lock actor, then workspace, then lifecycle children."""

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
from identity_workspace.persistence import (
    idempotency_records, invitations, memberships, outbox, users, workspace_cursors, workspaces,
)
from identity_workspace.workspaces.models import MAX_VERSION, deletion_event, public_workspace, require_match

CREATE_PATH = "/api/v1/workspaces"
CURSOR_SCOPE = "GET:/api/v1/workspaces:created_at-desc,id-desc:v1"
RETENTION = timedelta(hours=25)


def fingerprint(name):
    return hashlib.sha256(json.dumps({"name": name}, ensure_ascii=False,
                                    separators=(",", ":"), sort_keys=True).encode("utf-8")).digest()


def visible_query(actor_id):
    mine = memberships.alias("mine")
    count = select(func.count()).select_from(memberships).where(
        memberships.c.workspace_id == workspaces.c.id,
        memberships.c.revoked_at.is_(None),
    ).correlate(workspaces).scalar_subquery()
    return select(workspaces, mine.c.role.label("my_role"), count.label("member_count")).join(
        mine, (mine.c.workspace_id == workspaces.c.id) & (mine.c.user_id == actor_id)
        & mine.c.revoked_at.is_(None),
    ).where(workspaces.c.deleted_at.is_(None))


def visible(session, actor_id, identifier, *, lock=False):
    statement = visible_query(actor_id).where(workspaces.c.id == identifier)
    if lock:
        statement = statement.with_for_update(of=workspaces)
    row = session.execute(statement).mappings().one_or_none()
    if row is None:
        raise APIError(404)
    return row


def owner(row, actor):
    if row["owner_id"] != actor["id"] or row["my_role"] != "owner":
        raise APIError(403)


def owned_count(session, actor_id):
    return session.scalar(select(func.count()).select_from(workspaces).where(
        workspaces.c.owner_id == actor_id, workspaces.c.deleted_at.is_(None),
    ))


def sync_quota(session, actor_id):
    session.execute(update(users).where(users.c.id == actor_id).values(
        owned_workspace_count=owned_count(session, actor_id),
    ))


class WorkspaceStore:
    def __init__(self, database):
        self.database = database

    def create(self, hashed_token, name, key):
        with self.database.transaction() as session:
            # A bounded wait permits a safe retry instead of an unbounded request.
            session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
            actor = session_actor(session, hashed_token, lock=True)
            now = database_now(session)
            scope = (
                (idempotency_records.c.actor_id == actor["id"])
                & (idempotency_records.c.request_method == "POST")
                & (idempotency_records.c.canonical_path == CREATE_PATH)
                & (idempotency_records.c.idempotency_key == key)
            )
            saved = session.execute(select(idempotency_records).where(scope)).mappings().one_or_none()
            digest = fingerprint(name)
            if saved is not None and saved["expires_at"] > now:
                # Check access before disclosing the old response or a key conflict.
                visible(session, actor["id"], saved["resource_id"])
                if saved["fingerprint"] != digest:
                    raise APIError(409, code="IDEMPOTENCY_CONFLICT")
                return saved["response_body"], saved["etag"]
            if saved is not None:
                session.execute(delete(idempotency_records).where(scope))
            if owned_count(session, actor["id"]) >= 10:
                raise APIError(409, code="WORKSPACE_LIMIT_REACHED")
            identifier = uuid4()
            session.execute(workspaces.insert().values(
                id=identifier, name=name, owner_id=actor["id"], version=1,
                created_at=now, updated_at=now,
            ))
            session.execute(memberships.insert().values(
                id=uuid4(), workspace_id=identifier, user_id=actor["id"], role="owner", joined_at=now,
            ))
            sync_quota(session, actor["id"])
            body = public_workspace(visible(session, actor["id"], identifier))
            saved_at = database_now(session)
            session.execute(idempotency_records.insert().values(
                actor_id=actor["id"], request_method="POST", canonical_path=CREATE_PATH,
                idempotency_key=key, fingerprint=digest, resource_id=identifier,
                status_code=201, response_body=body, etag='"1"',
                created_at=saved_at, expires_at=saved_at + RETENTION,
            ))
            return body, '"1"'

    def list(self, hashed_token, limit=20, cursor=None):
        with self.database.transaction() as session:
            actor = session_actor(session, hashed_token)
            now = database_now(session)
            statement = visible_query(actor["id"])
            if cursor is not None:
                if re.fullmatch(r"[A-Za-z0-9_-]{43}", cursor) is None:
                    raise APIError(400, code="INVALID_CURSOR")
                position = session.execute(select(workspace_cursors).where(
                    workspace_cursors.c.token_hash == token_hash(cursor),
                    workspace_cursors.c.actor_id == actor["id"],
                    workspace_cursors.c.scope == CURSOR_SCOPE,
                    workspace_cursors.c.expires_at > now,
                )).mappings().one_or_none()
                if position is None:
                    raise APIError(400, code="INVALID_CURSOR")
                statement = statement.where(tuple_(workspaces.c.created_at, workspaces.c.id) < tuple_(
                    position["last_created_at"], position["last_id"],
                ))
            rows = session.execute(statement.order_by(
                workspaces.c.created_at.desc(), workspaces.c.id.desc(),
            ).limit(limit + 1)).mappings().all()
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = secrets.token_urlsafe(32)
                session.execute(workspace_cursors.insert().values(
                    token_hash=token_hash(next_cursor), actor_id=actor["id"], scope=CURSOR_SCOPE,
                    last_created_at=last["created_at"], last_id=last["id"],
                    created_at=now, expires_at=now + RETENTION,
                ))
            return {"items": [public_workspace(row) for row in rows[:limit]], "next_cursor": next_cursor}

    def read(self, hashed_token, identifier):
        with self.database.transaction() as session:
            actor = session_actor(session, hashed_token)
            body = public_workspace(visible(session, actor["id"], identifier))
            return body, f'"{body["version"]}"'

    def rename(self, hashed_token, identifier, name, matches):
        with self.database.transaction() as session:
            actor = session_actor(session, hashed_token, lock=True)
            row = visible(session, actor["id"], identifier, lock=True)
            owner(row, actor)
            require_match(matches, row["version"])
            if name != row["name"]:
                if row["version"] == MAX_VERSION:
                    raise APIError(412)
                session.execute(update(workspaces).where(workspaces.c.id == identifier).values(
                    name=name, version=row["version"] + 1, updated_at=database_now(session),
                ))
            body = public_workspace(visible(session, actor["id"], identifier))
            return body, f'"{body["version"]}"'

    def delete(self, hashed_token, identifier, matches):
        with self.database.transaction() as session:
            actor = session_actor(session, hashed_token, lock=True)
            row = visible(session, actor["id"], identifier, lock=True)
            owner(row, actor)
            require_match(matches, row["version"])
            if row["version"] == MAX_VERSION:
                raise APIError(412)
            now = database_now(session)
            version = row["version"] + 1
            session.execute(update(workspaces).where(workspaces.c.id == identifier).values(
                deleted_at=now, updated_at=now, version=version,
            ))
            session.execute(update(memberships).where(
                memberships.c.workspace_id == identifier, memberships.c.revoked_at.is_(None),
            ).values(revoked_at=now))
            session.execute(update(invitations).where(
                invitations.c.workspace_id == identifier, invitations.c.status == "pending",
            ).values(status="cancelled", updated_at=now))
            sync_quota(session, actor["id"])
            event = deletion_event(identifier, version, actor, now)
            session.execute(outbox.insert().values(
                event_id=UUID(event["id"]), event_type=event["type"], payload=event,
                created_at=now, next_attempt_at=now,
            ))

    def cleanup(self, limit=1000):
        """Bounded expiry cleanup. Never remove lifecycle history or outbox rows."""
        counts = {}
        with self.database.transaction() as session:
            for table in (idempotency_records, workspace_cursors):
                batch = select(table).where(table.c.expires_at <= func.clock_timestamp()).limit(
                    limit).with_for_update(skip_locked=True).cte("expired")
                predicates = [column == batch.c[column.name] for column in table.primary_key.columns]
                counts[table.name] = session.execute(delete(table).where(*predicates)).rowcount
        return counts
