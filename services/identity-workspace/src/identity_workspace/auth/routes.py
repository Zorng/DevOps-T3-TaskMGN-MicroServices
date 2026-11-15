import re

from fastapi import APIRouter, Request
from starlette.responses import Response

from identity_workspace.auth.models import Challenge, GoogleExchange, Me, Session
from identity_workspace.errors import APIError

router = APIRouter(tags=["Identity & Workspace API"])


def auth_service(request: Request):
    service = request.app.state.auth
    if service is None:
        raise APIError(503)
    return service


def allowed_origin(request: Request) -> str:
    origins = request.headers.getlist("origin")
    if len(origins) != 1 or origins[0] not in request.app.state.settings.allowed_origins:
        raise APIError(403, code="ORIGIN_NOT_ALLOWED")
    return origins[0]


def bearer_token(request: Request) -> str:
    values = request.headers.getlist("authorization")
    if not values:
        raise APIError(401, code="AUTH_REQUIRED")
    match = re.fullmatch(r"(?i:Bearer) +([A-Za-z0-9_-]{43})", values[0]) if len(values) == 1 else None
    if match is None:
        raise APIError(401, code="SESSION_INVALID")
    return match.group(1)


def client_address(request: Request) -> str:
    # Never trust arbitrary forwarded headers. The server/gateway must establish
    # a trusted peer address before proxy headers can be enabled in deployment.
    return request.client.host if request.client else "unknown"


@router.post("/api/v1/auth/google/challenges", status_code=201, response_model=Challenge, operation_id="createGoogleChallenge")
def create_google_challenge(request: Request):
    origin = allowed_origin(request)
    return auth_service(request).challenge(origin, client_address(request))


@router.post("/api/v1/auth/google/exchange", response_model=Session, operation_id="exchangeGoogleCredential")
def exchange_google_credential(body: GoogleExchange, request: Request):
    origin = allowed_origin(request)
    return auth_service(request).exchange(body.challenge_id, body.id_token, origin, client_address(request))


@router.post("/api/v1/auth/logout", status_code=204, operation_id="logout")
def logout(request: Request):
    token = bearer_token(request)
    auth_service(request).logout(token)
    return Response(status_code=204)


@router.get("/api/v1/me", response_model=Me, operation_id="getMe")
def get_me(request: Request):
    token = bearer_token(request)
    return auth_service(request).me(token)
