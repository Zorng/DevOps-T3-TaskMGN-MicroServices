from datetime import timedelta
import hashlib

import pytest
from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.auth.google import GoogleTokenInvalid, GoogleVerificationUnavailable, display_name
from identity_workspace.auth.models import timestamp
from identity_workspace.auth.service import AuthService, token_hash
from identity_workspace.errors import APIError


@pytest.fixture
def service(auth_dependencies):
    d = auth_dependencies
    return AuthService(d.store, d.verifier, d.settings, clock=lambda: d.now)


def test_challenge_has_256_random_bits_and_only_its_hash_reaches_storage(service, auth_dependencies):
    d = auth_dependencies
    result = service.challenge(d.settings.allowed_origins[0], "127.0.0.1")
    assert len(result.nonce) == 43
    assert result.expires_at == timestamp(d.now + timedelta(minutes=5))
    assert d.store.create_challenge.call_args.args == (token_hash(result.nonce), d.settings.allowed_origins[0])
    assert result.nonce not in repr(result)
    d.verifier.verify.assert_not_called()


def test_exchange_issues_random_session_and_sends_only_hash_to_atomic_storage(service, auth_dependencies):
    d = auth_dependencies
    result = service.exchange(d.identifier, "synthetic.jwt", d.settings.allowed_origins[0], "127.0.0.1")
    assert result.token_type == "Bearer" and len(result.access_token) == 43
    assert result.expires_at == timestamp(d.now + timedelta(hours=8))
    args = d.store.exchange.call_args.args
    assert args[0:3] == (d.identifier, token_hash(d.nonce), d.settings.allowed_origins[0])
    assert args[3].subject == d.claims["sub"]
    assert args[4] == hashlib.sha256(result.access_token.encode()).digest()
    assert result.access_token not in repr(result)


@pytest.mark.parametrize("damage", [
    {"iss": "https://invalid.example.com"}, {"iss": None}, {"aud": "another-client"},
    {"aud": []}, {"azp": "another-client"}, {"iat": None}, {"iat": True},
    {"iat": "100"}, {"iat": float("nan")}, {"exp": None}, {"exp": True},
    {"exp": float("inf")}, {"exp": 10 ** 1000}, {"sub": None}, {"sub": ""},
    {"sub": 123}, {"sub": "x" * 256}, {"nonce": None}, {"nonce": "wrong"},
    {"email_verified": False}, {"email_verified": "true"}, {"email": None},
    {"email": "invalid"}, {"email": "x" * 255}, {"nbf": 253402300799},
    {"nbf": True}, {"nbf": None}, {"nbf": float("nan")},
])
def test_invalid_claims_never_reach_session_creation(service, auth_dependencies, damage):
    d = auth_dependencies
    d.verifier.verify.return_value = {**d.claims, **damage}
    with pytest.raises(APIError) as error:
        service.exchange(d.identifier, "synthetic.jwt", d.settings.allowed_origins[0], "127.0.0.1")
    assert (error.value.status_code, error.value.code) == (401, "GOOGLE_TOKEN_INVALID")
    d.store.exchange.assert_not_called()


@pytest.mark.parametrize("key,offset", [("exp", 0), ("exp", -1), ("iat", 1)])
def test_expired_and_future_issued_tokens_are_rejected(service, auth_dependencies, key, offset):
    d = auth_dependencies
    d.verifier.verify.return_value = {**d.claims, key: d.now.timestamp() + offset}
    with pytest.raises(APIError, match="Authentication"):
        service.exchange(d.identifier, "synthetic.jwt", d.settings.allowed_origins[0], "127.0.0.1")
    d.store.exchange.assert_not_called()


@pytest.mark.parametrize("missing", ["iss", "aud", "iat", "exp", "sub", "nonce", "email", "email_verified"])
def test_every_required_claim_must_be_present(service, auth_dependencies, missing):
    d = auth_dependencies
    d.verifier.verify.return_value = {key: value for key, value in d.claims.items() if key != missing}
    with pytest.raises(APIError) as error:
        service.exchange(d.identifier, "synthetic.jwt", d.settings.allowed_origins[0], "127.0.0.1")
    assert error.value.code == "GOOGLE_TOKEN_INVALID"
    d.store.exchange.assert_not_called()


@pytest.mark.parametrize("issuer", ["accounts.google.com", "https://accounts.google.com"])
def test_valid_google_issuers_and_optional_claims_are_accepted(service, auth_dependencies, issuer):
    d = auth_dependencies
    d.verifier.verify.return_value = {**d.claims, "iss": issuer, "azp": d.settings.google_client_id, "nbf": d.now.timestamp() - 1}
    assert service.exchange(d.identifier, "synthetic.jwt", d.settings.allowed_origins[0], "127.0.0.1").user == d.user


@pytest.mark.parametrize("value,expected", [
    (None, "User"), (123, "User"), ("  ", "User"), ("<b>Example</b> Member", "Example Member"),
    ("<script>secret</script>", "User"), ("<![invalid]>", "User"), ("A\x00B", "AB"), ("Alice\nExample", "Alice Example"), ("x" * 250, "x" * 200),
])
def test_provider_names_become_bounded_nonempty_plain_text(value, expected):
    assert display_name(value) == expected


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1:5173"])
def test_unknown_expired_consumed_or_wrong_origin_challenge_is_rejected_before_google(service, auth_dependencies, origin):
    d = auth_dependencies
    d.store.challenge.return_value = None if origin is None else {**d.store.challenge.return_value, "origin": origin}
    with pytest.raises(APIError) as error:
        service.exchange(d.identifier, "synthetic.jwt", d.settings.allowed_origins[0], "127.0.0.1")
    assert error.value.code == "AUTH_CHALLENGE_INVALID"
    d.verifier.verify.assert_not_called()
    d.store.exchange.assert_not_called()


@pytest.mark.parametrize("failure,status,code", [
    (GoogleTokenInvalid(), 401, "GOOGLE_TOKEN_INVALID"),
    (GoogleVerificationUnavailable(), 503, "DEPENDENCY_UNAVAILABLE"),
    (RuntimeError("synthetic-sensitive-provider-detail"), 503, "DEPENDENCY_UNAVAILABLE"),
])
def test_provider_verification_failure_creates_no_session(service, auth_dependencies, failure, status, code):
    d = auth_dependencies
    d.verifier.verify.side_effect = failure
    with pytest.raises(APIError) as error:
        service.exchange(d.identifier, "synthetic.jwt", d.settings.allowed_origins[0], "127.0.0.1")
    assert (error.value.status_code, error.value.code) == (status, code)
    assert "synthetic-sensitive" not in str(error.value)
    d.store.exchange.assert_not_called()


@pytest.mark.parametrize("operation", ["challenge", "exchange", "me", "logout"])
def test_database_failure_is_a_safe_503_for_each_operation(service, auth_dependencies, operation):
    d = auth_dependencies
    failure = SQLAlchemyError("synthetic-sensitive-database-detail")
    if operation in {"challenge", "exchange"}:
        d.store.rate_limit.side_effect = failure
    elif operation == "me":
        d.store.current_user.side_effect = failure
    else:
        d.store.logout.side_effect = failure
    calls = {
        "challenge": lambda: service.challenge(d.settings.allowed_origins[0], "127.0.0.1"),
        "exchange": lambda: service.exchange(d.identifier, "synthetic.jwt", d.settings.allowed_origins[0], "127.0.0.1"),
        "me": lambda: service.me("S" * 43), "logout": lambda: service.logout("S" * 43),
    }
    with pytest.raises(APIError) as error:
        calls[operation]()
    assert error.value.status_code == 503
    assert "synthetic-sensitive" not in str(error.value)
    d.store.exchange.assert_not_called()


def test_current_user_uses_only_hash_and_returns_stored_owned_count(service, auth_dependencies):
    d = auth_dependencies
    d.store.current_user.return_value = (d.user, 3)
    assert service.me("S" * 43).owned_workspace_count == 3
    d.store.current_user.assert_called_once_with(token_hash("S" * 43))


def test_logout_revokes_presented_token_hash_only(service, auth_dependencies):
    service.logout("S" * 43)
    auth_dependencies.store.logout.assert_called_once_with(token_hash("S" * 43))


@pytest.mark.parametrize("operation", ["me", "logout"])
def test_expired_or_revoked_session_is_rejected(service, auth_dependencies, operation):
    d = auth_dependencies
    target = d.store.current_user if operation == "me" else d.store.logout
    target.side_effect = APIError(401, code="SESSION_INVALID")
    with pytest.raises(APIError) as error:
        getattr(service, operation)("S" * 43)
    assert error.value.code == "SESSION_INVALID"


def test_unconfigured_google_does_not_create_challenges_or_sessions(auth_dependencies):
    d = auth_dependencies
    settings = d.settings.model_copy(update={"google_client_id": None})
    service = AuthService(d.store, d.verifier, settings)
    with pytest.raises(APIError) as error:
        service.challenge(settings.allowed_origins[0], "127.0.0.1")
    assert error.value.status_code == 503
    d.store.create_challenge.assert_not_called()
