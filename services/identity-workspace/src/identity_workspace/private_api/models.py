"""Closed private projections matching the shared internal OpenAPI schemas."""
from typing import Literal

from pydantic import Field

from identity_workspace.memberships.models import Member, UserSummary
from identity_workspace.validation import ContractModel
from identity_workspace.workspaces.models import MAX_VERSION


class SessionIdentity(ContractModel):
    user: UserSummary
    session_expires_at: str


class AccessContext(ContractModel):
    actor: UserSummary
    workspace_id: str
    workspace_version: int = Field(ge=1, le=MAX_VERSION)
    actor_membership_id: str
    actor_role: Literal["owner", "member"]
    members: list[Member] = Field(min_length=1, max_length=20)
