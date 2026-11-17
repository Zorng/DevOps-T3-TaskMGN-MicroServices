"""Transaction-local primitives shared with invitation/access implementations.

Callers supply one Database.transaction() session and let failures roll it back.
Mutators lock actor user first, workspace next, and invitation/member rows last.
Joining here grants no authorization: invitation acceptance must independently
validate its recipient, active workspace and pending invitation in that transaction.
"""

from uuid import uuid4

from sqlalchemy import func, select, update

from identity_workspace.auth.store import database_now
from identity_workspace.errors import APIError
from identity_workspace.memberships.models import public_member
from identity_workspace.persistence import memberships, users, workspaces
from identity_workspace.workspaces.models import MAX_VERSION


def locked_workspace(session, identifier, *, write=False):
    statement = select(workspaces).where(
        workspaces.c.id == identifier, workspaces.c.deleted_at.is_(None),
    ).with_for_update(read=not write)
    row = session.execute(statement).mappings().one_or_none()
    if row is None:
        raise APIError(404)
    return row


def current_membership(session, workspace_id, user_id, *, lock=False):
    statement = select(memberships).where(
        memberships.c.workspace_id == workspace_id, memberships.c.user_id == user_id,
        memberships.c.revoked_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    return session.execute(statement).mappings().one_or_none()


def require_membership(session, workspace_id, actor_id):
    member = current_membership(session, workspace_id, actor_id)
    if member is None:
        raise APIError(404)
    return member


def active_members(session, workspace_id, query=""):
    statement = select(
        memberships.c.id, memberships.c.user_id, memberships.c.role, memberships.c.joined_at,
        users.c.display_name,
    ).join(users, users.c.id == memberships.c.user_id).where(
        memberships.c.workspace_id == workspace_id, memberships.c.revoked_at.is_(None),
    )
    if query:
        # Bound parameter; %, _ and the escape character are literal text.
        statement = statement.where(users.c.display_name.icontains(query, autoescape=True))
    rows = session.execute(statement.order_by(
        memberships.c.joined_at.asc(), memberships.c.id.asc(),
    ).limit(20)).mappings().all()
    return [public_member(row) for row in rows]


def bump_workspace(session, workspace, now):
    if workspace["version"] >= MAX_VERSION:
        raise APIError(503)
    version = workspace["version"] + 1
    session.execute(update(workspaces).where(workspaces.c.id == workspace["id"]).values(
        version=version, updated_at=now,
    ))
    return version


def initialize_owner(session, workspace_id, owner_id, now):
    """Only the newly created workspace's initial owner; leaves version at one."""
    workspace = locked_workspace(session, workspace_id, write=True)
    count = session.scalar(select(func.count()).select_from(memberships).where(
        memberships.c.workspace_id == workspace_id,
    ))
    if workspace["owner_id"] != owner_id or count != 0 or workspace["version"] != 1:
        raise APIError(503)
    identifier = uuid4()
    session.execute(memberships.insert().values(
        id=identifier, workspace_id=workspace_id, user_id=owner_id, role="owner", joined_at=now,
    ))
    return identifier


def join_member(session, workspace_id, user_id):
    """Insert a fresh member generation after caller authorizes invitation acceptance.

    Shares the caller's transaction; never commits an invitation independently.
    A repeat accepted invitation must be handled before calling this primitive.
    """
    workspace = locked_workspace(session, workspace_id, write=True)
    if current_membership(session, workspace_id, user_id) is not None:
        raise APIError(409, code="ALREADY_MEMBER")
    count = session.scalar(select(func.count()).select_from(memberships).where(
        memberships.c.workspace_id == workspace_id, memberships.c.revoked_at.is_(None),
    ))
    if count >= 20:
        raise APIError(409, code="MEMBER_LIMIT_REACHED")
    if workspace["version"] >= MAX_VERSION:
        raise APIError(503)
    now, identifier = database_now(session), uuid4()
    session.execute(memberships.insert().values(
        id=identifier, workspace_id=workspace_id, user_id=user_id, role="member", joined_at=now,
    ))
    bump_workspace(session, workspace, now)
    return identifier


def access_snapshot(session, workspace_id, actor_id):
    """Fresh generation/version-consistent projection for future private endpoints.

    Session and service-credential authentication remain the caller's responsibility.
    Hold the workspace SHARE lock until the caller's transaction completes.
    """
    workspace = locked_workspace(session, workspace_id)
    actor_membership = require_membership(session, workspace_id, actor_id)
    members = active_members(session, workspace_id)
    actor = next(member["user"] for member in members if member["user"]["id"] == str(actor_id))
    return {
        "actor": actor, "workspace_id": str(workspace_id), "workspace_version": workspace["version"],
        "actor_membership_id": str(actor_membership["id"]), "actor_role": actor_membership["role"],
        "members": members,
    }
