"""Internal POST checks. Neither route accepts a body or browser CORS."""
from fastapi import APIRouter, Request

from identity_workspace.auth.routes import bearer_token
from identity_workspace.errors import APIError
from identity_workspace.validation import UUIDValue

router = APIRouter(prefix="/internal/v1")


def service(request):
    value = request.app.state.private_identity
    if value is None:
        raise APIError(503)
    return value


@router.post("/auth/introspect")
def introspect(request: Request):
    return service(request).introspect(request.headers.getlist("x-service-key"), bearer_token(request))


@router.post("/workspaces/{workspace_id}/access")
def workspace_access(request: Request, workspace_id: UUIDValue):
    return service(request).workspace_access(request.headers.getlist("x-service-key"), bearer_token(request), workspace_id)
