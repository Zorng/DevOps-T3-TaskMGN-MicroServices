"""Pure ASGI transport, route-input, correlation, and CORS enforcement."""

import json
from time import perf_counter
from typing import get_args

from fastapi.routing import iter_route_contexts
from pydantic import BaseModel
from starlette.datastructures import Headers, MutableHeaders, QueryParams
from starlette.responses import Response
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from identity_workspace.correlation import normalize_request_id, request_id_context
from identity_workspace.errors import APIError, ErrorDetail, error_response

MAX_BODY_BYTES = 65536
CORS_HEADERS = {"authorization", "content-type", "idempotency-key", "if-match", "x-request-id"}
SAFE_HEADERS = {"accept", "accept-language", "content-language"}


def model_fields(annotation, seen=None) -> set[str]:
    seen = set() if seen is None else seen
    if annotation in seen:
        return set()
    seen.add(annotation)
    names = set()
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        for name, field in annotation.model_fields.items():
            names.add(field.alias or name)
            names.update(model_fields(field.annotation, seen))
    for argument in get_args(annotation):
        names.update(model_fields(argument, seen))
    return names


def route_fields(route) -> tuple[set[str], set[str]]:
    queries, declared = set(), set()

    def walk(dependant):
        for field in getattr(dependant, "query_params", []):
            annotation = field.field_info.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                queries.update(item.alias or name for name, item in annotation.model_fields.items())
            else:
                queries.add(field.alias)
        for group in ("query_params", "body_params", "path_params", "header_params"):
            for field in getattr(dependant, group, []):
                declared.add(field.alias)
                declared.update(model_fields(field.field_info.annotation))
        for dependency in getattr(dependant, "dependencies", []):
            walk(dependency)

    walk(getattr(route, "dependant", None))
    return queries, declared


def find_route(routes, scope):
    partial = None
    for route in iter_route_contexts(routes):
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return route, True
        if match == Match.PARTIAL and partial is None:
            partial = route
    return partial, False


def validate_query(scope, allowed):
    if set(QueryParams(scope.get("query_string", b""))) - allowed:
        raise APIError(400, details=[ErrorDetail(field="query", reason="Unsupported query parameters.")])


def reject_non_json_constant(value):
    raise ValueError("Non-JSON numeric constant.")


def validate_body(body: bytes, headers: Headers, expects_body: bool):
    if not body:
        return
    if not expects_body:
        raise APIError(400, details=[ErrorDetail(field="body", reason="This operation does not accept a body.")])
    content_types = headers.getlist("content-type")
    if len(content_types) > 1:
        raise APIError(400)
    content_type = (content_types[0] if content_types else "").split(";")
    if content_type[0].strip().lower() != "application/json":
        raise APIError(415)
    for parameter in content_type[1:]:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset" and value.strip().strip('"').lower() not in {"utf-8", "utf8"}:
            raise APIError(415)
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise APIError(415)
    try:
        json.loads(body.decode("utf-8"), parse_constant=reject_non_json_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise APIError(400) from None


class ContractHTTPMiddleware:
    def __init__(self, app: ASGIApp, *, routes, allowed_origins: tuple[str, ...], observability=None):
        self.app = app
        self.routes = routes
        self.allowed_origins = frozenset(allowed_origins)
        self.observability = observability

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        request_started = perf_counter()
        identifiers = headers.getlist("x-request-id")
        identifier = normalize_request_id(identifiers[0] if len(identifiers) == 1 else None)
        token = request_id_context.set(identifier)
        state = scope.setdefault("state", {})
        state["request_id"] = identifier
        state["declared_fields"] = set()
        state["route_template"] = "unmatched"
        origin = None
        started = False
        response_status = None
        failure_category = None

        async def send_headers(message: Message):
            nonlocal started, response_status
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = identifier
                response_headers["Cache-Control"] = "no-store"
                for name in ("access-control-allow-origin", "access-control-allow-credentials", "access-control-expose-headers"):
                    if name in response_headers:
                        del response_headers[name]
                if headers.get("origin") is not None:
                    vary = response_headers.get("vary", "")
                    if "origin" not in {part.strip().lower() for part in vary.split(",")}:
                        response_headers["Vary"] = ", ".join(filter(None, [vary, "Origin"]))
                if origin in self.allowed_origins:
                    response_headers["Access-Control-Allow-Origin"] = origin
                    response_headers["Access-Control-Expose-Headers"] = "ETag, X-Request-ID, Retry-After"
                started = True
                response_status = message["status"]
            await send(message)

        try:
            lengths = headers.getlist("content-length")
            declared_length = None
            if lengths:
                if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
                    raise APIError(400)
                length = lengths[0].lstrip("0") or "0"
                if len(length) > 5 or int(length) > MAX_BODY_BYTES:
                    raise APIError(413)
                declared_length = int(length)
            origins = headers.getlist("origin")
            if origins and scope["path"].startswith("/internal/"):
                raise APIError(403, code="ORIGIN_NOT_ALLOWED", message="Browser origins cannot call private service routes.")
            if len(origins) > 1:
                raise APIError(400)
            if origins:
                if origins[0] not in self.allowed_origins:
                    raise APIError(403, code="ORIGIN_NOT_ALLOWED", message="The request origin is not allowed.")
                origin = origins[0]

            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    raise APIError(400)
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    raise APIError(413)
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            if declared_length is not None and declared_length != len(body):
                raise APIError(400)

            preflight = scope["method"] == "OPTIONS" and headers.get("access-control-request-method") is not None
            target_scope = dict(scope)
            if preflight:
                target_scope["method"] = headers["access-control-request-method"]
            route, full_match = find_route(self.routes, target_scope)
            if route is not None:
                state["route_template"] = route.path_format
            if preflight:
                if origin is None:
                    raise APIError(403, code="ORIGIN_NOT_ALLOWED", message="An allowed origin is required.")
                if not scope["path"].startswith("/api/v1/") or route is None:
                    raise APIError(404)
                if not full_match:
                    await error_response(APIError(405), headers={"Allow": ", ".join(sorted(route.methods))})(scope, receive, send_headers)
                    return
                if body:
                    raise APIError(400)
                requested_headers = {
                    value.strip().lower()
                    for value in headers.get("access-control-request-headers", "").split(",") if value.strip()
                }
                if requested_headers - CORS_HEADERS - SAFE_HEADERS:
                    raise APIError(400)
                allowed_query, _ = route_fields(route)
                validate_query(scope, allowed_query)
                response = Response(status_code=204, headers={
                    "Access-Control-Allow-Methods": ", ".join(sorted(route.methods)),
                    "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key, If-Match, X-Request-ID",
                })
                await response(scope, receive, send_headers)
                return

            if route is not None and full_match:
                allowed_query, declared = route_fields(route)
                state["declared_fields"] = declared
                validate_query(scope, allowed_query)
                validate_body(bytes(body), headers, getattr(route, "body_field", None) is not None)
            replayed = False

            async def replay_body():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, replay_body, send_headers)
        except APIError as error:
            if started:
                raise
            await error_response(error)(scope, receive, send_headers)
        except Exception:
            failure_category = "response_failed" if started else "internal_error"
            if started:
                raise
            await error_response(APIError(500))(scope, receive, send_headers)
        finally:
            try:
                if self.observability is not None:
                    self.observability.record_request(
                        method=scope["method"], route=state["route_template"],
                        status_code=response_status, duration_seconds=perf_counter() - request_started,
                        error_category=failure_category,
                    )
            finally:
                request_id_context.reset(token)
