"""Six public invitation operations; email lookup is only in the create body."""
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BeforeValidator
from starlette.responses import JSONResponse

from identity_workspace.auth.routes import bearer_token
from identity_workspace.errors import APIError
from identity_workspace.invitations.models import InviteCreate, InvitationResponse, STATUSES
from identity_workspace.validation import parse_uuid
from identity_workspace.workspaces.routes import idempotency_key

router = APIRouter(prefix="/api/v1")


def path_identifier(value):
    try:
        return parse_uuid(value)
    except ValueError:
        raise APIError(400) from None


Identifier = Annotated[UUID, BeforeValidator(path_identifier)]


def service(request):
    value = request.app.state.invitations
    if value is None:
        raise APIError(503)
    return value


def pagination(request, limit, cursor, status):
    if any(len(request.query_params.getlist(name)) > 1 for name in ("limit", "cursor", "status")):
        raise APIError(400)
    if re.fullmatch(r"[0-9]{1,3}", limit) is None or not 1 <= int(limit) <= 100:
        raise APIError(400)
    if cursor is not None and len(cursor) > 1024:
        raise APIError(400, code="INVALID_CURSOR")
    if status is not None and status not in STATUSES:
        raise APIError(400)
    return int(limit), cursor, status


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
def create_invitation(request: Request, workspace_id: Identifier, body: InviteCreate):
    token = bearer_token(request)
    return JSONResponse(service(request).create(token, workspace_id, body.email, idempotency_key(request)), status_code=201)


@router.get("/workspaces/{workspace_id}/invitations")
def list_owned_invitations(request: Request, workspace_id: Identifier,
                           limit: Annotated[str, Query()] = "20", cursor: str | None = None, status: str | None = None):
    token = bearer_token(request)
    return service(request).list_owned(token, workspace_id, *pagination(request, limit, cursor, status))


@router.get("/invitations")
def list_received_invitations(request: Request, limit: Annotated[str, Query()] = "20",
                             cursor: str | None = None, status: str | None = None):
    token = bearer_token(request)
    return service(request).list_received(token, *pagination(request, limit, cursor, status))


@router.get("/invitations/{invitation_id}")
def read_invitation(request: Request, invitation_id: Identifier):
    return service(request).read(bearer_token(request), invitation_id)


@router.post("/invitations/{invitation_id}/response")
def respond_invitation(request: Request, invitation_id: Identifier, body: InvitationResponse):
    return service(request).respond(bearer_token(request), invitation_id, body.action)


@router.post("/invitations/{invitation_id}/cancel")
def cancel_invitation(request: Request, invitation_id: Identifier):
    return service(request).cancel(bearer_token(request), invitation_id)
