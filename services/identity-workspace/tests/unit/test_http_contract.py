import asyncio
from unittest.mock import Mock
from uuid import uuid4

import pytest
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from identity_workspace.correlation import request_id_context
from identity_workspace.errors import APIError, validation_details
from identity_workspace.http_contract import ContractHTTPMiddleware, MAX_BODY_BYTES, validate_body


def test_validation_details_do_not_echo_inputs_exception_messages_or_unknown_keys():
    details = validation_details([
        {"type": "value_error", "loc": ("body", "name"), "msg": "synthetic-secret", "input": "synthetic-secret", "ctx": {"error": "synthetic-secret"}},
        {"type": "extra_forbidden", "loc": ("body", "synthetic-secret"), "input": "synthetic-secret"},
        {"type": "int_parsing", "loc": ("body", "mapping", "synthetic-secret"), "input": "synthetic-secret"},
    ], {"name", "mapping"})
    assert [item.field for item in details] == ["name", "body", "mapping"]
    assert "synthetic-secret" not in str(details)


def test_validation_details_are_bounded():
    assert len(validation_details([{"type": "missing", "loc": ("body", "name")}] * 100, {"name"})) == 50


@pytest.mark.parametrize("status,code", [(409, None), (503, "FORBIDDEN"), (400, "UNDEFINED_CODE")])
def test_public_error_codes_must_match_their_contract_status(status, code):
    with pytest.raises(ValueError):
        APIError(status, code=code)


@pytest.mark.parametrize("body,content_type,status", [
    (b"{}", "text/plain", 415), (b"{}", "", 415),
    (b"{}", "application/json; charset=utf-16", 415),
    (b"{", "application/json", 400), (b"\xff", "application/json", 400),
    (b'{"value":NaN}', "application/json", 400),
])
def test_body_validation_rejects_invalid_transport(body, content_type, status):
    with pytest.raises(APIError) as caught:
        validate_body(body, Headers({"content-type": content_type}), True)
    assert caught.value.status_code == status


def test_empty_body_needs_no_content_type_and_bodyless_routes_reject_bodies():
    validate_body(b"", Headers({"content-type": "text/plain"}), False)
    with pytest.raises(APIError) as caught:
        validate_body(b"{}", Headers({"content-type": "application/json"}), False)
    assert caught.value.status_code == 400


def test_utf8_content_type_is_supported():
    validate_body('{"name":"😀"}'.encode(), Headers({"content-type": 'application/json; charset="UTF-8"'}), True)


def test_duplicate_content_types_are_malformed():
    with pytest.raises(APIError) as caught:
        validate_body(b"{}", Headers(raw=[(b"content-type", b"application/json"), (b"content-type", b"text/plain")]), True)
    assert caught.value.status_code == 400


def run_asgi(chunks, headers=(), *, fail=False):
    received, sent = [], []

    async def inner(scope, receive, send):
        if fail:
            raise RuntimeError("synthetic-secret-from-handler")
        message = await receive()
        received.append(message["body"])
        response = JSONResponse({"length": len(message["body"])}, headers={"Cache-Control": "public", "X-Request-ID": "wrong"})
        await response(scope, receive, send)

    async def exercise():
        queue = list(chunks)

        async def receive():
            return queue.pop(0) if queue else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": list(headers)}
        await ContractHTTPMiddleware(inner, routes=[], allowed_origins=())(scope, receive, send)
        assert request_id_context.get() is None

    asyncio.run(exercise())
    return received, sent


def response_status(sent):
    return next(message["status"] for message in sent if message["type"] == "http.response.start")


def test_streamed_body_is_counted_without_content_length():
    received, sent = run_asgi([
        {"type": "http.request", "body": b"a" * 32768, "more_body": True},
        {"type": "http.request", "body": b"b" * 32769, "more_body": False},
    ])
    assert not received
    assert response_status(sent) == 413


def test_exact_body_limit_is_replayed_once_and_headers_are_overridden():
    identifier = str(uuid4())
    received, sent = run_asgi([
        {"type": "http.request", "body": b"a" * 32768, "more_body": True},
        {"type": "http.request", "body": b"b" * 32768, "more_body": False},
    ], [(b"x-request-id", identifier.encode())])
    assert received == [b"a" * 32768 + b"b" * 32768]
    assert response_status(sent) == 200
    headers = Headers(raw=sent[0]["headers"])
    assert headers["x-request-id"] == identifier
    assert headers["cache-control"] == "no-store"


@pytest.mark.parametrize("length,status", [(b"65537", 413), (b"-1", 400), (b"invalid", 400), (b"9" * 5000, 413)])
def test_invalid_content_length_does_not_call_the_handler(length, status):
    received, sent = run_asgi([], [(b"content-length", length)])
    assert received == []
    assert response_status(sent) == status


def test_content_length_cannot_hide_an_oversized_actual_body():
    received, sent = run_asgi([
        {"type": "http.request", "body": b"a" * (MAX_BODY_BYTES + 1), "more_body": False},
    ], [(b"content-length", b"1")])
    assert not received
    assert response_status(sent) == 413


def test_content_length_mismatch_is_malformed():
    received, sent = run_asgi([
        {"type": "http.request", "body": b"a", "more_body": False},
    ], [(b"content-length", b"2")])
    assert not received
    assert response_status(sent) == 400


def test_duplicate_lengths_are_rejected():
    received, sent = run_asgi([], [(b"content-length", b"1"), (b"content-length", b"1")])
    assert not received
    assert response_status(sent) == 400


def test_zero_padded_content_length_is_counted_without_large_integer_parsing():
    received, sent = run_asgi([
        {"type": "http.request", "body": b"a", "more_body": False},
    ], [(b"content-length", b"0" * 5000 + b"1")])
    assert received == [b"a"]
    assert response_status(sent) == 200


def test_client_disconnect_does_not_call_handler_or_send_response():
    received, sent = run_asgi([{"type": "http.disconnect"}])
    assert received == sent == []


def test_unhandled_exception_is_safe_and_correlation_context_is_reset():
    received, sent = run_asgi([{"type": "http.request", "body": b"", "more_body": False}], fail=True)
    assert response_status(sent) == 500
    assert "synthetic-secret" not in str(sent)


def test_non_http_scopes_are_delegated_without_http_changes():
    async def exercise():
        seen = []

        async def inner(scope, receive, send):
            seen.append(scope)

        scope = {"type": "lifespan"}
        await ContractHTTPMiddleware(inner, routes=[], allowed_origins=())(scope, Mock(), Mock())
        assert seen == [scope]

    asyncio.run(exercise())
