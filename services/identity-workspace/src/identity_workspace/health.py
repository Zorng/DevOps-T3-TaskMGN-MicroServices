"""Private operational health endpoints and dependency-independent health logic."""

from dataclasses import dataclass
from typing import Literal
from time import perf_counter

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from identity_workspace.database import DatabaseResources


class HealthBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ok", "unavailable"]


@dataclass(frozen=True)
class HealthResult:
    status_code: int
    status: Literal["ok", "unavailable"]


def liveness_result() -> HealthResult:
    return HealthResult(200, "ok")


def readiness_result(database: DatabaseResources | None) -> HealthResult:
    if database is None:
        return HealthResult(503, "unavailable")
    try:
        ready = database.check_readiness()
    except Exception:
        # A probe error means unavailable. Never return or log a driver exception
        # containing connection credentials or SQL values from this boundary.
        return HealthResult(503, "unavailable")
    return HealthResult(200, "ok") if ready is True else HealthResult(503, "unavailable")


def health_response(result: HealthResult) -> JSONResponse:
    return JSONResponse(
        content=HealthBody(status=result.status).model_dump(),
        status_code=result.status_code,
    )


router = APIRouter(tags=["Identity & Workspace API"])


@router.get("/health/live", operation_id="Identitylive", response_model=HealthBody)
async def live() -> JSONResponse:
    return health_response(liveness_result())


@router.get(
    "/health/ready", operation_id="Identityready", response_model=HealthBody,
    responses={503: {"model": HealthBody}},
)
async def ready(request: Request) -> JSONResponse:
    # Blocking database work runs in the thread pool, leaving liveness responsive.
    started = perf_counter()
    result = await run_in_threadpool(readiness_result, request.app.state.database)
    request.app.state.observability.record_readiness(result.status_code == 200, perf_counter() - started)
    return health_response(result)
