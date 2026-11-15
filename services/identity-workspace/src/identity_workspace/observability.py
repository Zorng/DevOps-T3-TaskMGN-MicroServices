"""Private Prometheus registry, request/probe instrumentation, and metrics route."""

from collections.abc import Callable
import logging
import math
import time
from importlib.metadata import version
from typing import TextIO

from fastapi import APIRouter, Request
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, Info, generate_latest
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from identity_workspace.structured_logging import ApplicationLogger, safe_method

STATUS_CATEGORIES = {
    400: "invalid_request", 401: "authentication_required", 403: "forbidden",
    404: "not_found", 405: "method_not_allowed", 409: "conflict",
    413: "payload_too_large", 415: "unsupported_media_type", 422: "validation_failed",
    429: "rate_limited", 499: "client_disconnected", 500: "internal_error",
    503: "dependency_unavailable",
}


def safe_duration(value: float) -> float:
    return max(0.0, value) if type(value) in {int, float} and math.isfinite(value) else 0.0


class Observability:
    def __init__(self, level: str, route_names: Callable[[], set[str]], *, stream: TextIO | None = None):
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "identity_http_requests_total", "Completed HTTP requests, including internal 499 disconnect observations.",
            ["method", "route", "status_code"], registry=self.registry,
        )
        self.duration = Histogram(
            "identity_http_request_duration_seconds", "Time through completion of the HTTP response.",
            ["method", "route"], registry=self.registry,
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
        )
        self.errors = Counter("identity_http_errors_total", "HTTP client/server errors.", ["kind"], registry=self.registry)
        self.errors.labels(kind="client").inc(0)
        self.errors.labels(kind="server").inc(0)
        self.readiness = Gauge("identity_readiness_status", "Last completed probe: -1 unknown, 0 unavailable, 1 ready.", registry=self.registry)
        self.readiness_timestamp = Gauge("identity_readiness_last_check_timestamp_seconds", "Unix timestamp of last completed readiness probe; 0 before any probe.", registry=self.registry)
        self.readiness_checks = Counter("identity_readiness_checks_total", "Completed readiness probes.", ["result"], registry=self.registry)
        self.readiness_duration = Histogram("identity_readiness_duration_seconds", "Readiness probe duration.", ["result"], registry=self.registry)
        self.log_failures = Counter("identity_log_write_failures_total", "Structured log formatting/output failures.", registry=self.registry)
        Info("identity_service", "Service version.", registry=self.registry).info({"version": version("identity-workspace-service")})
        self.reset_readiness()
        self.log = ApplicationLogger(level, route_names, stream=stream, on_failure=self.log_failures.inc)

    def reset_readiness(self):
        self.readiness.set(-1)
        self.readiness_timestamp.set(0)

    def record_request(self, *, method, route, status_code, duration_seconds, error_category=None):
        method = safe_method(method)
        route = self.log.formatter.safe_route(route)
        status = status_code if type(status_code) is int and 100 <= status_code <= 599 else 499
        duration = safe_duration(duration_seconds)
        self.requests.labels(method=method, route=route, status_code=str(status)).inc()
        self.duration.labels(method=method, route=route).observe(duration)
        if status >= 500 or error_category == "response_failed":
            self.errors.labels(kind="server").inc()
            level = logging.ERROR
        elif status >= 400:
            self.errors.labels(kind="client").inc()
            level = logging.WARNING
        else:
            level = logging.INFO
        category = error_category or STATUS_CATEGORIES.get(status)
        self.log.event(
            level, "http_request_disconnected" if status == 499 else "http_request_completed",
            method=method, route=route, status_code=status,
            duration_ms=duration * 1000, error_category=category,
        )

    def record_readiness(self, ready: bool, duration_seconds: float):
        result = "ready" if ready else "unavailable"
        duration = safe_duration(duration_seconds)
        self.readiness.set(1 if ready else 0)
        self.readiness_timestamp.set(time.time())
        self.readiness_checks.labels(result=result).inc()
        self.readiness_duration.labels(result=result).observe(duration)
        self.log.event(
            logging.INFO if ready else logging.WARNING, "readiness_checked",
            route="/health/ready", readiness=result, duration_ms=duration * 1000,
            error_category=None if ready else "storage_unavailable",
        )

    def render_metrics(self) -> bytes:
        return generate_latest(self.registry)

    def close(self):
        self.log.close()


def metrics_response(observability: Observability) -> Response:
    return Response(content=observability.render_metrics(), headers={"Content-Type": CONTENT_TYPE_LATEST})


router = APIRouter(tags=["Operations"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    # No database/broker calls during a scrape; an outage must not hide metrics.
    return await run_in_threadpool(metrics_response, request.app.state.observability)
