import json
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt, jwt
from google.auth.transport.requests import Request
import pytest
import requests

from identity_workspace.auth.google import GoogleTokenInvalid, GoogleTokenVerifier, GoogleVerificationUnavailable


@pytest.fixture(scope="module")
def signing_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return crypt.RSASigner.from_string(private), public.decode()


@pytest.fixture
def verifier(auth_dependencies, signing_key, monkeypatch):
    response = SimpleNamespace(status=200, data=json.dumps({"synthetic-key": signing_key[1]}).encode())
    fetch = Mock(return_value=response)
    monkeypatch.setattr(Request, "__call__", fetch)
    verifier = GoogleTokenVerifier(auth_dependencies.settings)
    yield verifier, fetch
    verifier.close()


def signed_token(signing_key, claims):
    return jwt.encode(signing_key[0], claims, key_id="synthetic-key").decode()


def test_supported_google_library_verifies_a_real_rsa_signature_offline(verifier, signing_key, auth_dependencies):
    adapter, fetch = verifier
    claims = adapter.verify(signed_token(signing_key, auth_dependencies.claims))
    assert claims["sub"] == auth_dependencies.claims["sub"]
    assert fetch.call_args.kwargs["timeout"] == 2
    assert fetch.call_args.kwargs["allow_redirects"] is False
    assert fetch.call_args.args[0] == "https://www.googleapis.com/oauth2/v1/certs"


def test_invalid_signature_is_rejected(verifier, signing_key, auth_dependencies):
    token = signed_token(signing_key, auth_dependencies.claims)
    head, body, signature = token.split(".")
    signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(GoogleTokenInvalid):
        verifier[0].verify(".".join([head, body, signature]))


@pytest.mark.parametrize("claim", ["aud", "iss", "exp", "iat"])
def test_real_library_rejects_wrong_audience_issuer_expiry_or_issued_at(verifier, signing_key, auth_dependencies, claim):
    d = auth_dependencies
    values = {"aud": "wrong-client", "iss": "https://invalid.example.com", "exp": int(d.now.timestamp()) - 5, "iat": int(d.now.timestamp()) + 600}
    with pytest.raises(GoogleTokenInvalid):
        verifier[0].verify(signed_token(signing_key, {**d.claims, claim: values[claim]}))


@pytest.mark.parametrize("token", ["not-a-jwt", "synthetic.invalid.token", "", "a.b.c.d"])
def test_malformed_jwt_is_rejected(verifier, token):
    with pytest.raises(GoogleTokenInvalid):
        verifier[0].verify(token)


def test_transport_failure_is_a_safe_dependency_failure(verifier, signing_key, auth_dependencies):
    verifier[1].side_effect = requests.Timeout("synthetic-sensitive-provider-token")
    with pytest.raises(GoogleVerificationUnavailable) as error:
        verifier[0].verify(signed_token(signing_key, auth_dependencies.claims))
    assert "synthetic-sensitive" not in str(error.value)


@pytest.mark.parametrize("status,body", [(503, b"unavailable"), (200, b"not-json"), (200, b"{}"), (200, b"[]"), (200, b'{"key":null}'), (200, b'{"keys":[]}'), (200, b'{"synthetic-key":"not a certificate"}')])
def test_bad_provider_key_responses_are_unavailable(verifier, signing_key, auth_dependencies, status, body):
    verifier[1].return_value = SimpleNamespace(status=status, data=body)
    with pytest.raises(GoogleVerificationUnavailable):
        verifier[0].verify(signed_token(signing_key, auth_dependencies.claims))


def test_busy_provider_transport_has_a_bounded_wait(auth_dependencies):
    settings = auth_dependencies.settings.model_copy(update={"google_timeout_seconds": 0.01})
    verifier = GoogleTokenVerifier(settings)
    verifier._lock.acquire()
    try:
        with pytest.raises(GoogleVerificationUnavailable):
            verifier.verify("synthetic.jwt")
    finally:
        verifier._lock.release()
        verifier.close()


def test_provider_session_is_disposed_on_shutdown(auth_dependencies):
    verifier = GoogleTokenVerifier(auth_dependencies.settings)
    session = Mock()
    verifier._session = session
    verifier.close()
    session.close.assert_called_once()
    assert verifier._session is None
