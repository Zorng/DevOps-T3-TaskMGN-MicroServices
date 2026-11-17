"""Keep configuration tests independent of real machine/local credentials."""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.upper().startswith("IDENTITY_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def require_google_transport_test_doubles(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must replace Google verification or its HTTP transport.")
    monkeypatch.setattr("requests.sessions.Session.request", blocked)


@pytest.fixture
def settings():
    from identity_workspace.config import Settings

    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://example_user:synthetic_password@localhost:5432/example_db",
    )


@pytest.fixture
def auth_dependencies(settings):
    from identity_workspace.auth.models import User, timestamp
    from identity_workspace.auth.service import token_hash
    from identity_workspace.auth.store import AuthStore

    settings = settings.model_copy(update={
        "google_client_id": "synthetic-client.apps.googleusercontent.com",
        "allowed_origins": ("http://localhost:5173", "http://127.0.0.1:5173"),
    })
    now = datetime.now(timezone.utc).replace(microsecond=0)
    nonce = "N" * 43
    identifier = uuid4()
    claims = {
        "iss": "https://accounts.google.com", "aud": settings.google_client_id,
        "iat": int(now.timestamp()) - 10, "exp": int(now.timestamp()) + 3600,
        "sub": "synthetic-google-subject-one", "nonce": nonce,
        "email": "member@example.com", "email_verified": True, "name": "Example Member",
    }
    user = User(id=str(uuid4()), display_name="Example Member", email="member@example.com", created_at=timestamp(now))
    store = Mock(spec=AuthStore)
    store.rate_limit.return_value = None
    store.create_challenge.return_value = SimpleNamespace(id=identifier, expires_at=now + timedelta(minutes=5))
    store.challenge.return_value = {
        "id": identifier, "nonce_hash": token_hash(nonce), "origin": settings.allowed_origins[0],
        "expires_at": now + timedelta(minutes=5),
    }
    store.exchange.return_value = (user, now + timedelta(hours=8))
    store.current_user.return_value = (user, 0)
    verifier = Mock()
    verifier.verify.return_value = claims
    return SimpleNamespace(
        settings=settings, store=store, verifier=verifier, now=now,
        nonce=nonce, identifier=identifier, claims=claims, user=user,
    )


@pytest.fixture
def workspace_dependencies(settings):
    from identity_workspace.workspaces.store import WorkspaceStore

    settings = settings.model_copy(update={"allowed_origins": ("http://localhost:5173",)})
    identifier, actor = uuid4(), uuid4()
    body = {
        "id": str(identifier), "name": "Team", "owner_id": str(actor), "my_role": "owner",
        "member_count": 1, "version": 1, "created_at": "2026-11-16T00:00:00.000Z",
        "updated_at": "2026-11-16T00:00:00.000Z",
    }
    store = Mock(spec=WorkspaceStore)
    store.create.return_value = (body, '"1"')
    store.read.return_value = (body, '"1"')
    store.rename.return_value = ({**body, "name": "Renamed", "version": 2}, '"2"')
    store.list.return_value = {"items": [body], "next_cursor": None}
    store.delete.return_value = None
    return SimpleNamespace(settings=settings, store=store, identifier=identifier, actor=actor, body=body, key=uuid4())


@pytest.fixture
def membership_dependencies(settings):
    from identity_workspace.memberships.store import MembershipStore

    settings = settings.model_copy(update={"allowed_origins": ("http://localhost:5173",)})
    workspace_id, owner_id, user_id, generation_id = uuid4(), uuid4(), uuid4(), uuid4()
    member = {
        "membership_id": str(generation_id), "user": {"id": str(user_id), "display_name": "Example Member"},
        "role": "member", "joined_at": "2026-11-17T00:00:00.000Z",
    }
    store = Mock(spec=MembershipStore)
    store.list.return_value = {"items": [member]}
    store.remove.return_value = store.leave.return_value = None
    store.access_snapshot.return_value = {
        "actor": member["user"], "workspace_id": str(workspace_id), "workspace_version": 2,
        "actor_membership_id": str(generation_id), "actor_role": "member", "members": [member],
    }
    return SimpleNamespace(settings=settings, store=store, workspace_id=workspace_id,
                           owner_id=owner_id, user_id=user_id, generation_id=generation_id, member=member)


@pytest.fixture
def invitation_dependencies(settings):
    from identity_workspace.invitations.store import InvitationStore

    settings = settings.model_copy(update={"allowed_origins": ("http://localhost:5173",)})
    identifier, workspace_id, owner_id, recipient_id = uuid4(), uuid4(), uuid4(), uuid4()
    body = {
        "id": str(identifier), "workspace_id": str(workspace_id), "workspace_name": "Example Workspace",
        "inviter": {"id": str(owner_id), "display_name": "Example Owner"},
        "recipient": {"id": str(recipient_id), "display_name": "Example Recipient"},
        "status": "pending", "created_at": "2026-11-17T00:00:00.000Z", "updated_at": "2026-11-17T00:00:00.000Z",
    }
    store = Mock(spec=InvitationStore)
    store.create.return_value = store.read.return_value = body
    store.list_owned.return_value = store.list_received.return_value = {"items": [body], "next_cursor": None}
    store.respond.return_value = {**body, "status": "accepted"}
    store.cancel.return_value = {**body, "status": "cancelled"}
    return SimpleNamespace(settings=settings, store=store, identifier=identifier, workspace_id=workspace_id,
                           owner_id=owner_id, recipient_id=recipient_id, body=body, key=uuid4())


@pytest.fixture
def private_dependencies(settings):
    import json
    from pathlib import Path
    from pydantic import SecretStr
    from identity_workspace.private_api.store import PrivateIdentityStore

    fixtures = json.loads((Path(__file__).resolve().parents[1] / "fixtures/private-identity.json").read_text())
    task_key, notification_key = "T" * 43, "N" * 43
    settings = settings.model_copy(update={"task_service_key": SecretStr(task_key),
                                           "notification_service_key": SecretStr(notification_key),
                                           "allowed_origins": ("http://localhost:5173",)})
    store = Mock(spec=PrivateIdentityStore)
    store.introspect.return_value = fixtures["cases"][0]["response"]
    store.workspace_access.return_value = fixtures["cases"][2]["response"]
    return SimpleNamespace(settings=settings, store=store, task_key=task_key, notification_key=notification_key,
                           workspace_id=store.workspace_access.return_value["workspace_id"], fixtures=fixtures)
