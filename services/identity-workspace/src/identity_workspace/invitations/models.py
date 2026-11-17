"""Invitation inputs, public projections and a credential-free creation event."""
from typing import Literal
from uuid import uuid4

from email_validator import EmailNotValidError, validate_email
from pydantic import Field, field_validator

from identity_workspace.auth.models import timestamp
from identity_workspace.correlation import request_id_context
from identity_workspace.errors import APIError
from identity_workspace.validation import ContractModel, InvitationEmailText

STATUSES = {"pending", "accepted", "declined", "cancelled"}


class InviteCreate(ContractModel):
    email: InvitationEmailText = Field(repr=False)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        try:
            validate_email(value, check_deliverability=False)
        except EmailNotValidError:
            raise ValueError("Must be a valid email address.") from None
        # Only trim/case normalization; dots and plus aliases remain distinct.
        return value.lower()


class InvitationResponse(ContractModel):
    action: Literal["accept", "decline"]


def transition(current, requested):
    """True means perform the transition, False means replay its terminal record."""
    if current == requested:
        return False
    if current != "pending":
        raise APIError(409, code="INVITATION_NOT_PENDING")
    return True


def public_invitation(row):
    return {
        "id": str(row["id"]), "workspace_id": str(row["workspace_id"]),
        "workspace_name": row["workspace_name"],
        "inviter": {"id": str(row["inviter_id"]), "display_name": row["inviter_name"]},
        "recipient": {"id": str(row["recipient_id"]), "display_name": row["recipient_name"]},
        "status": row["status"], "created_at": timestamp(row["created_at"]),
        "updated_at": timestamp(row["updated_at"]),
    }


def created_event(identifier, workspace_id, recipient_id, actor, now):
    return {
        "id": str(uuid4()), "type": "invitation.created.v1", "schema_version": 1,
        "producer": "identity-workspace", "occurred_at": timestamp(now),
        "request_id": request_id_context.get() or str(uuid4()),
        "aggregate_id": str(identifier), "aggregate_version": 1,
        "data": {"workspace_id": str(workspace_id), "invitation_id": str(identifier),
                 "recipient_id": str(recipient_id),
                 "actor": {"id": str(actor["id"]), "display_name": actor["display_name"]}},
    }
