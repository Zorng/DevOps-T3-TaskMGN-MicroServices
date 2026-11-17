"""Public projections and immutable, credential-free deletion events."""

from typing import Literal
from uuid import uuid4

from pydantic import Field

from identity_workspace.auth.models import timestamp
from identity_workspace.correlation import request_id_context
from identity_workspace.errors import APIError
from identity_workspace.validation import ContractModel, WorkspaceName

MAX_VERSION = 9007199254740991


class WorkspaceNameBody(ContractModel):
    name: WorkspaceName


class Workspace(ContractModel):
    id: str
    name: WorkspaceName
    owner_id: str
    my_role: Literal["owner", "member"]
    member_count: int = Field(ge=1, le=20)
    version: int = Field(ge=1, le=MAX_VERSION)
    created_at: str
    updated_at: str


class WorkspacePage(ContractModel):
    items: list[Workspace] = Field(max_length=100)
    next_cursor: str | None


def public_workspace(row):
    return Workspace(
        id=str(row["id"]), name=row["name"], owner_id=str(row["owner_id"]),
        my_role=row["my_role"], member_count=row["member_count"], version=row["version"],
        created_at=timestamp(row["created_at"]), updated_at=timestamp(row["updated_at"]),
    ).model_dump()


def require_match(values: list[str], version: int):
    # Deliberately evaluated after authentication, visibility and ownership.
    import re
    if not values:
        raise APIError(428)
    if len(values) != 1 or re.fullmatch(r'"[1-9][0-9]*"', values[0]) is None:
        raise APIError(400)
    if values[0] != f'"{version}"':
        raise APIError(412)


def deletion_event(identifier, version, actor, now):
    # A closed projection: Google subject, email and credentials never enter events.
    return {
        "id": str(uuid4()), "type": "workspace.deleted.v1", "schema_version": 1,
        "producer": "identity-workspace", "occurred_at": timestamp(now),
        "request_id": request_id_context.get() or str(uuid4()),
        "aggregate_id": str(identifier), "aggregate_version": version,
        "data": {"workspace_id": str(identifier), "actor": {
            "id": str(actor["id"]), "display_name": actor["display_name"],
        }},
    }
