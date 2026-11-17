"""Fresh authoritative reads; no positive session/access cache or provider calls."""
from sqlalchemy import func, select

from identity_workspace.auth.models import timestamp
from identity_workspace.errors import APIError
from identity_workspace.memberships.store import MembershipStore
from identity_workspace.persistence import sessions, users


class PrivateIdentityStore:
    def __init__(self, database):
        self.database = database
        self.memberships = MembershipStore(database)

    def introspect(self, hashed_token):
        with self.database.transaction() as session:
            row = session.execute(select(users.c.id, users.c.display_name, sessions.c.expires_at).join(
                sessions, sessions.c.user_id == users.c.id,
            ).where(sessions.c.token_hash == hashed_token, sessions.c.revoked_at.is_(None),
                    sessions.c.expires_at > func.clock_timestamp())).mappings().one_or_none()
            if row is None:
                raise APIError(401, code="SESSION_INVALID")
            return {"user": {"id": str(row["id"]), "display_name": row["display_name"]},
                    "session_expires_at": timestamp(row["expires_at"])}

    def workspace_access(self, hashed_token, workspace_id):
        return self.memberships.access_snapshot(hashed_token, workspace_id)
