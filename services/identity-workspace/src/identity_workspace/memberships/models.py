"""Closed member/event projections: display names, never an email directory."""

from typing import Literal
from uuid import uuid4

from pydantic import Field

from identity_workspace.auth.models import timestamp
from identity_workspace.correlation import request_id_context
from identity_workspace.errors import APIError
from identity_workspace.validation import ContractModel


class UserSummary(ContractModel):
    id: str
    display_name: str = Field(min_length=1, max_length=200)


class Member(ContractModel):
    membership_id: str
    user: UserSummary
    role: Literal["owner", "member"]
    joined_at: str


class MemberList(ContractModel):
    items: list[Member] = Field(max_length=20)


def search_text(value):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 100 or "\x00" in value:
        raise APIError(400)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise APIError(400) from None
    return value


def public_member(row):
    return Member(
        membership_id=str(row["id"]),
        user=UserSummary(id=str(row["user_id"]), display_name=row["display_name"]),
        role=row["role"], joined_at=timestamp(row["joined_at"]),
    ).model_dump()


def require_owner(workspace, actor_membership, actor_id):
    if workspace["owner_id"] != actor_id or actor_membership["role"] != "owner":
        raise APIError(403)


def require_non_owner(workspace, membership):
    if membership["user_id"] == workspace["owner_id"] or membership["role"] == "owner":
        raise APIError(409, code="OWNER_CANNOT_LEAVE")


def removal_event(workspace_id, version, membership, actor, reason, now):
    if reason not in {"removed", "left"}:
        raise ValueError("Unknown membership removal reason.")
    return {
        "id": str(uuid4()), "type": "workspace.member_removed.v1", "schema_version": 1,
        "producer": "identity-workspace", "occurred_at": timestamp(now),
        "request_id": request_id_context.get() or str(uuid4()),
        "aggregate_id": str(workspace_id), "aggregate_version": version,
        "data": {
            "workspace_id": str(workspace_id), "user_id": str(membership["user_id"]),
            "membership_id": str(membership["id"]), "reason": reason,
            "actor": {"id": str(actor["id"]), "display_name": actor["display_name"]},
        },
    }
