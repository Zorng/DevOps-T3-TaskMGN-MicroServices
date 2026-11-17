"""Public Identity workspace endpoints; application sessions are mandatory."""

from typing import Annotated
import re

from fastapi import APIRouter, Query, Request, Response
from starlette.responses import JSONResponse

from identity_workspace.auth.routes import bearer_token
from identity_workspace.errors import APIError
from identity_workspace.validation import UUIDValue, parse_uuid
from identity_workspace.workspaces.models import WorkspaceNameBody

router = APIRouter(prefix="/api/v1/workspaces")


def service(request):
    value = request.app.state.workspaces
    if value is None:
        raise APIError(503)
    return value


def idempotency_key(request):
    values = request.headers.getlist("idempotency-key")
    if not values:
        raise APIError(400, code="IDEMPOTENCY_KEY_REQUIRED")
    if len(values) != 1:
        raise APIError(400)
    try:
        return parse_uuid(values[0])
    except ValueError:
        raise APIError(400) from None


@router.get("")
def list_workspaces(request: Request, limit: Annotated[str, Query()] = "20",
                    cursor: Annotated[str | None, Query()] = None):
    token = bearer_token(request)
    if any(len(request.query_params.getlist(name)) > 1 for name in ("limit", "cursor")):
        raise APIError(400)
    if re.fullmatch(r"[0-9]{1,3}", limit) is None or not 1 <= int(limit) <= 100:
        raise APIError(400)
    if cursor is not None and len(cursor) > 1024:
        raise APIError(400, code="INVALID_CURSOR")
    return service(request).list(token, int(limit), cursor)


@router.post("", status_code=201)
def create_workspace(request: Request, body: WorkspaceNameBody):
    token = bearer_token(request)
    data, etag = service(request).create(token, body.name, idempotency_key(request))
    return JSONResponse(data, status_code=201, headers={"ETag": etag})


@router.get("/{workspace_id}")
def read_workspace(request: Request, workspace_id: UUIDValue):
    data, etag = service(request).read(bearer_token(request), workspace_id)
    return JSONResponse(data, headers={"ETag": etag})


@router.patch("/{workspace_id}")
def rename_workspace(request: Request, workspace_id: UUIDValue, body: WorkspaceNameBody):
    data, etag = service(request).rename(
        bearer_token(request), workspace_id, body.name, request.headers.getlist("if-match"),
    )
    return JSONResponse(data, headers={"ETag": etag})


@router.delete("/{workspace_id}", status_code=204)
def delete_workspace(request: Request, workspace_id: UUIDValue):
    service(request).delete(bearer_token(request), workspace_id, request.headers.getlist("if-match"))
    return Response(status_code=204)
