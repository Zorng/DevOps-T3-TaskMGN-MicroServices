from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text, update

from identity_workspace.auth.service import AuthService, token_hash
from identity_workspace.auth.store import AuthStore
from identity_workspace.errors import APIError
from identity_workspace.main import create_app
from identity_workspace.persistence import auth_challenges, auth_rate_limits, sessions, users

ORIGIN = "http://localhost:5173"


@pytest.fixture
def authentication(database, postgres, auth_dependencies):
    d = auth_dependencies
    settings = postgres.settings(
        google_client_id=d.settings.google_client_id, allowed_origins=d.settings.allowed_origins,
    )
    d.settings = settings
    d.credentials = {}
    d.verifier.verify.side_effect = lambda token: dict(d.credentials[token])
    d.service = AuthService(AuthStore(database), d.verifier, settings)
    return d


def pending(d, *, subject=None, email=None, name=None):
    challenge = d.service.challenge(ORIGIN, "127.0.0.1")
    credential = "synthetic-google-credential-" + uuid4().hex
    claims = {**d.claims, "nonce": challenge.nonce}
    for key, value in (("sub", subject), ("email", email), ("name", name)):
        if value is not None:
            claims[key] = value
    d.credentials[credential] = claims
    return UUID(challenge.challenge_id), credential, challenge


def exchange(d, pair):
    identifier, credential, _ = pair
    return d.service.exchange(identifier, credential, ORIGIN, "127.0.0.1")


def count(database, table):
    with database.transaction() as session:
        return session.scalar(select(func.count()).select_from(table))


def test_real_database_sign_in_hash_storage_and_exact_lifetimes(database, authentication):
    d = authentication
    pair = pending(d)
    result = exchange(d, pair)
    assert result.user.email == "member@example.com"
    assert d.service.me(result.access_token).user == result.user
    with database.transaction() as session:
        challenge = session.execute(select(auth_challenges)).mappings().one()
        stored = session.execute(select(sessions)).mappings().one()
        assert challenge["expires_at"] - challenge["created_at"] == timedelta(minutes=5)
        assert stored["expires_at"] - stored["created_at"] == timedelta(hours=8)
        assert stored["token_hash"] == token_hash(result.access_token)
        assert challenge["nonce_hash"] == token_hash(pair[2].nonce)
        assert challenge["consumed_at"] is not None
        assert result.access_token not in repr(stored)
        assert pair[1] not in repr(stored) + repr(challenge)


def test_complete_http_sign_in_current_user_and_logout_flow(database, authentication):
    d = authentication
    logs = StringIO()
    app = create_app(
        d.settings.model_copy(update={"log_level": "DEBUG"}), database_factory=Mock(return_value=database),
        google_verifier_factory=Mock(return_value=d.verifier), log_stream=logs,
    )
    credential = "synthetic-complete-flow-google-credential"
    with TestClient(app) as client:
        challenge = client.post("/api/v1/auth/google/challenges", headers={"Origin": ORIGIN})
        assert challenge.status_code == 201
        data = challenge.json()
        d.credentials[credential] = {**d.claims, "nonce": data["nonce"]}
        response = client.post("/api/v1/auth/google/exchange", headers={"Origin": ORIGIN}, json={"challenge_id": data["challenge_id"], "id_token": credential})
        assert response.status_code == 200
        session = response.json()
        bearer = {"Authorization": "Bearer " + session["access_token"]}
        current = client.get("/api/v1/me", headers=bearer)
        assert current.status_code == 200
        assert current.json() == {"user": session["user"], "owned_workspace_count": 0}
        assert client.post("/api/v1/auth/logout", headers=bearer).status_code == 204
        assert client.get("/api/v1/me", headers=bearer).status_code == 401
        assert client.post("/api/v1/auth/logout", headers=bearer).status_code == 401
    d.verifier.verify.assert_called_once_with(credential)
    assert all(value not in logs.getvalue() for value in (credential, data["nonce"], session["access_token"], d.claims["sub"]))
    assert count(database, users) == count(database, sessions) == 1


def test_same_subject_updates_profile_without_changing_user_or_owned_count(database, authentication):
    d = authentication
    first = exchange(d, pending(d))
    with database.transaction() as session:
        session.execute(update(users).values(owned_workspace_count=3))
    second = exchange(d, pending(d, email="renamed@example.com", name="Updated Member"))
    assert second.user.id == first.user.id and second.user.created_at == first.user.created_at
    assert second.user.email == "renamed@example.com" and second.user.display_name == "Updated Member"
    assert d.service.me(first.access_token).user == second.user
    assert d.service.me(second.access_token).owned_workspace_count == 3
    assert count(database, users) == 1 and count(database, sessions) == 2


def test_distinct_google_subjects_are_never_merged_by_verified_email(database, authentication):
    d = authentication
    first = exchange(d, pending(d))
    second = exchange(d, pending(d, subject="another-google-subject"))
    assert first.user.email == second.user.email
    assert first.user.id != second.user.id
    assert count(database, users) == 2


def test_replayed_exchange_cannot_create_a_second_session(database, authentication):
    d = authentication
    pair = pending(d)
    exchange(d, pair)
    with pytest.raises(APIError) as error:
        exchange(d, pair)
    assert error.value.code == "AUTH_CHALLENGE_INVALID"
    assert count(database, sessions) == count(database, users) == 1


def test_eight_concurrent_http_exchanges_consume_one_challenge_once(database, authentication):
    d = authentication
    pair = pending(d)
    rendezvous = Barrier(8, timeout=15)

    def verify(token):
        rendezvous.wait()
        return dict(d.credentials[token])

    d.verifier.verify.side_effect = verify
    app = create_app(
        d.settings, database_factory=Mock(return_value=database),
        google_verifier_factory=Mock(return_value=d.verifier),
    )
    with TestClient(app) as client:
        def send(_):
            return client.post("/api/v1/auth/google/exchange", headers={"Origin": ORIGIN}, json={"challenge_id": str(pair[0]), "id_token": pair[1]})
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(send, range(8)))
    assert sum(response.status_code == 200 for response in responses) == 1
    rejected = [response for response in responses if response.status_code != 200]
    assert len(rejected) == 7
    assert all(response.status_code == 401 and response.json()["error"]["code"] == "AUTH_CHALLENGE_INVALID" for response in rejected)
    assert count(database, sessions) == count(database, users) == 1


def test_concurrent_first_logins_for_same_subject_create_one_user(database, authentication):
    d = authentication
    pairs = [pending(d), pending(d)]
    rendezvous = Barrier(2, timeout=10)

    def verify(token):
        rendezvous.wait()
        return dict(d.credentials[token])

    d.verifier.verify.side_effect = verify
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: exchange(d, pair), pairs))
    assert results[0].user.id == results[1].user.id
    assert results[0].access_token != results[1].access_token
    assert count(database, users) == 1 and count(database, sessions) == 2


@pytest.mark.parametrize("state", ["unknown", "expired", "consumed", "wrong_origin"])
def test_invalid_challenge_states_create_no_user_or_session(database, authentication, state):
    d = authentication
    pair = pending(d)
    with database.transaction() as session:
        now = session.scalar(select(func.clock_timestamp()))
        if state == "unknown":
            pair = uuid4(), pair[1], pair[2]
        elif state == "expired":
            session.execute(update(auth_challenges).values(created_at=now - timedelta(minutes=6), expires_at=now - timedelta(minutes=1)))
        elif state == "consumed":
            session.execute(update(auth_challenges).values(consumed_at=now))
        else:
            session.execute(update(auth_challenges).values(origin="http://127.0.0.1:5173"))
    with pytest.raises(APIError) as error:
        exchange(d, pair)
    assert error.value.code == "AUTH_CHALLENGE_INVALID"
    assert count(database, sessions) == count(database, users) == 0
    d.verifier.verify.assert_not_called()


def test_expiry_is_rechecked_after_provider_verification(database, authentication):
    d = authentication
    pair = pending(d)

    def verify(token):
        with database.transaction() as session:
            now = session.scalar(select(func.clock_timestamp()))
            session.execute(update(auth_challenges).values(created_at=now - timedelta(minutes=6), expires_at=now - timedelta(minutes=1)))
        return dict(d.credentials[token])

    d.verifier.verify.side_effect = verify
    with pytest.raises(APIError) as error:
        exchange(d, pair)
    assert error.value.code == "AUTH_CHALLENGE_INVALID"
    assert count(database, sessions) == count(database, users) == 0


def test_provider_outage_keeps_challenge_usable_and_creates_no_session(database, authentication):
    from identity_workspace.auth.google import GoogleVerificationUnavailable

    d = authentication
    pair = pending(d)
    d.verifier.verify.side_effect = GoogleVerificationUnavailable()
    with pytest.raises(APIError) as error:
        exchange(d, pair)
    assert error.value.status_code == 503
    assert count(database, sessions) == count(database, users) == 0
    with database.transaction() as session:
        assert session.scalar(select(auth_challenges.c.consumed_at)) is None
    d.verifier.verify.side_effect = lambda token: dict(d.credentials[token])
    assert exchange(d, pair).user.email == "member@example.com"


def test_session_insert_failure_rolls_back_user_and_challenge_consumption(database, authentication):
    d = authentication
    pair = pending(d)
    with database.transaction() as session:
        session.execute(text("REVOKE INSERT ON identity_workspace.sessions FROM identity_app"))
    try:
        assert database.check_readiness() is False
        with pytest.raises(APIError) as error:
            exchange(d, pair)
        assert error.value.status_code == 503
        assert count(database, sessions) == count(database, users) == 0
        with database.transaction() as session:
            assert session.scalar(select(auth_challenges.c.consumed_at)) is None
    finally:
        with database.transaction() as session:
            session.execute(text("GRANT INSERT ON identity_workspace.sessions TO identity_app"))
    assert database.check_readiness() is True
    assert exchange(d, pair).access_token


def test_logout_revokes_only_presented_session_and_repeated_logout_fails(database, authentication):
    d = authentication
    first = exchange(d, pending(d))
    second = exchange(d, pending(d))
    d.service.logout(first.access_token)
    for operation in (d.service.me, d.service.logout):
        with pytest.raises(APIError) as error:
            operation(first.access_token)
        assert error.value.code == "SESSION_INVALID"
    assert d.service.me(second.access_token).user.id == first.user.id
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(sessions).where(sessions.c.revoked_at.is_not(None))) == 1


@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_expired_and_revoked_sessions_are_rejected_by_real_http_routes(database, authentication, state):
    d = authentication
    result = exchange(d, pending(d))
    with database.transaction() as session:
        now = session.scalar(select(func.clock_timestamp()))
        changes = {"created_at": now - timedelta(hours=9), "expires_at": now - timedelta(hours=1)} if state == "expired" else {"revoked_at": now}
        session.execute(update(sessions).values(**changes))
    app = create_app(d.settings, database_factory=Mock(return_value=database), google_verifier_factory=Mock(return_value=d.verifier))
    with TestClient(app) as client:
        for method, path in (("get", "/api/v1/me"), ("post", "/api/v1/auth/logout")):
            response = client.request(method, path, headers={"Authorization": "Bearer " + result.access_token})
            assert response.status_code == 401 and response.json()["error"]["code"] == "SESSION_INVALID"


def test_database_ip_rate_limit_is_atomic_across_service_instances(database, authentication):
    d = authentication
    settings = d.settings.model_copy(update={"auth_challenge_limit_per_minute": 3})
    services = [AuthService(AuthStore(database), d.verifier, settings) for _ in range(8)]

    def start(service):
        try:
            return service.challenge(ORIGIN, "127.0.0.1")
        except APIError as error:
            assert error.status_code == 429 and 1 <= error.retry_after <= 60
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(start, services))
    assert sum(result is not None for result in results) == count(database, auth_challenges) == 3
    assert services[0].challenge(ORIGIN, "127.0.0.2")
    with database.transaction() as session:
        session.execute(update(auth_rate_limits).values(window_started=func.clock_timestamp() - timedelta(seconds=61)))
    assert services[0].challenge(ORIGIN, "127.0.0.1")


def test_cleanup_removes_only_expired_or_revoked_authentication_data(database, authentication):
    d = authentication
    expired = exchange(d, pending(d))
    revoked = exchange(d, pending(d))
    active = exchange(d, pending(d))
    pending(d)
    with database.transaction() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(update(sessions).where(sessions.c.token_hash == token_hash(expired.access_token)).values(created_at=now - timedelta(hours=9), expires_at=now - timedelta(hours=1)))
        session.execute(update(auth_challenges).values(created_at=now - timedelta(minutes=6), expires_at=now - timedelta(minutes=1)))
        session.execute(update(auth_rate_limits).values(window_started=now - timedelta(hours=2)))
    d.service.logout(revoked.access_token)
    first = d.service.store.cleanup(limit=1)
    assert all(value <= 1 for value in first.values())
    d.service.store.cleanup()
    assert count(database, sessions) == 1 and count(database, auth_challenges) == count(database, auth_rate_limits) == 0
    assert d.service.me(active.access_token).user.id == active.user.id


def test_auth_migration_upgrades_existing_foundation_without_changing_outbox(database, migrate):
    from identity_workspace.persistence import outbox

    examples = json.loads((Path(__file__).resolve().parents[4] / "api_contracts/examples/events.json").read_text())
    event = dict(next(item for item in examples if item["type"] == "invitation.created.v1"))
    event["id"] = str(uuid4())
    with database.transaction() as session:
        session.execute(outbox.insert().values(event_id=UUID(event["id"]), event_type=event["type"], payload=event))
    try:
        migrate("downgrade", "0001_identity_foundation")
        migrate()
        migrate("check")
        assert count(database, outbox) == 1
        assert count(database, users) == count(database, sessions) == 0
    finally:
        migrate()
