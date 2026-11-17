"""Public member listing/search, owner removal and non-owner leave."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from identity_workspace.auth.routes import bearer_token
from identity_workspace.errors import APIError
from identity_workspace.validation import UUIDValue

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}")


def service(request):
    value = request.app.state.memberships
    if value is None:
        raise APIError(503)
    return value


@router.get("/members")
def list_members(request: Request, workspace_id: UUIDValue,
                 q: Annotated[str | None, Query()] = None):
    token = bearer_token(request)
    if len(request.query_params.getlist("q")) > 1:
        raise APIError(400)
    return service(request).list(token, workspace_id, q)


@router.delete("/members/{user_id}", status_code=204)
def remove_member(request: Request, workspace_id: UUIDValue, user_id: UUIDValue):
    service(request).remove(bearer_token(request), workspace_id, user_id)
    return Response(status_code=204)


@router.delete("/membership", status_code=204)
def leave_workspace(request: Request, workspace_id: UUIDValue):
    service(request).leave(bearer_token(request), workspace_id)
    return Response(status_code=204)
