"""Membership revocation and its exact-generation event commit together."""

from uuid import UUID

from sqlalchemy import update

from identity_workspace.auth.store import database_now, session_actor
from identity_workspace.memberships.models import removal_event, require_non_owner, require_owner
from identity_workspace.memberships.primitives import (
    access_snapshot, active_members, bump_workspace, current_membership, locked_workspace, require_membership,
)
from identity_workspace.persistence import memberships, outbox


class MembershipStore:
    def __init__(self, database):
        self.database = database

    def _actor_workspace(self, session, hashed_token, workspace_id, *, write=False):
        actor = session_actor(session, hashed_token, lock=write)
        workspace = locked_workspace(session, workspace_id, write=write)
        # Fresh validation after any parent-lock wait; never rely on cached access.
        actor = session_actor(session, hashed_token)
        member = require_membership(session, workspace_id, actor["id"])
        return actor, workspace, member

    def list(self, hashed_token, workspace_id, query=""):
        with self.database.transaction() as session:
            self._actor_workspace(session, hashed_token, workspace_id)
            return {"items": active_members(session, workspace_id, query)}

    def remove(self, hashed_token, workspace_id, user_id):
        with self.database.transaction() as session:
            actor, workspace, mine = self._actor_workspace(session, hashed_token, workspace_id, write=True)
            require_owner(workspace, mine, actor["id"])
            target = current_membership(session, workspace_id, user_id, lock=True)
            if target is None:
                return
            require_non_owner(workspace, target)
            self._revoke(session, workspace, target, actor, "removed")

    def leave(self, hashed_token, workspace_id):
        with self.database.transaction() as session:
            actor, workspace, mine = self._actor_workspace(session, hashed_token, workspace_id, write=True)
            require_non_owner(workspace, mine)
            self._revoke(session, workspace, mine, actor, "left")

    def _revoke(self, session, workspace, target, actor, reason):
        now = database_now(session)
        bump_workspace(session, workspace, now)
        session.execute(update(memberships).where(
            memberships.c.id == target["id"], memberships.c.revoked_at.is_(None),
        ).values(revoked_at=now))
        event = removal_event(workspace["id"], target, actor, reason, now)
        session.execute(outbox.insert().values(
            event_id=UUID(event["id"]), event_type=event["type"], payload=event,
            created_at=now, next_attempt_at=now,
        ))

    def access_snapshot(self, hashed_token, workspace_id):
        with self.database.transaction() as session:
            actor, _, _ = self._actor_workspace(session, hashed_token, workspace_id)
            return access_snapshot(session, workspace_id, actor["id"])
