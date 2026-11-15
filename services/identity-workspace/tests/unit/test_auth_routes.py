from types import SimpleNamespace

import pytest
from starlette.requests import Request

from identity_workspace.auth.routes import allowed_origin, bearer_token, client_address
from identity_workspace.errors import APIError


def request(settings, headers):
    return Request({
        "type": "http", "headers": [(key.lower().encode(), value.encode()) for key, value in headers],
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
        "client": ("127.0.0.1", 8001),
    })


def test_allowed_origin_is_exact_and_peer_address_is_not_forwarded_input(auth_dependencies):
    d = auth_dependencies
    incoming = request(d.settings, [("Origin", d.settings.allowed_origins[0]), ("X-Forwarded-For", "203.0.113.7")])
    assert allowed_origin(incoming) == d.settings.allowed_origins[0]
    assert client_address(incoming) == "127.0.0.1"


@pytest.mark.parametrize("values", [[], ["null"], ["https://denied.example.com"], ["http://localhost:5173", "http://localhost:5173"]])
def test_missing_disallowed_or_duplicate_origins_are_denied(auth_dependencies, values):
    with pytest.raises(APIError) as error:
        allowed_origin(request(auth_dependencies.settings, [("Origin", value) for value in values]))
    assert error.value.status_code == 403 and error.value.code == "ORIGIN_NOT_ALLOWED"


@pytest.mark.parametrize("values,code", [
    ([], "AUTH_REQUIRED"), ([""], "SESSION_INVALID"), (["Basic synthetic"], "SESSION_INVALID"),
    (["Bearer synthetic.invalid.jwt"], "SESSION_INVALID"),
    (["Bearer " + "S" * 43] * 2, "SESSION_INVALID"),
])
def test_missing_or_malformed_authorization_is_rejected(auth_dependencies, values, code):
    with pytest.raises(APIError) as error:
        bearer_token(request(auth_dependencies.settings, [("Authorization", value) for value in values]))
    assert error.value.code == code


def test_bearer_scheme_is_case_insensitive(auth_dependencies):
    assert bearer_token(request(auth_dependencies.settings, [("Authorization", "bEaReR " + "S" * 43)])) == "S" * 43
