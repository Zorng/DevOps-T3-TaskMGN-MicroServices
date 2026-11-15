"""Authentication application behavior with injectable persistence/provider boundaries."""

from datetime import datetime, timezone
import hashlib
import secrets

from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.auth.google import GoogleTokenInvalid, validate_claims
from identity_workspace.auth.models import Challenge, Me, Session, timestamp
from identity_workspace.errors import APIError


def token_hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii")).digest()


class AuthService:
    def __init__(self, store, verifier, settings, *, clock=None):
        self.store = store
        self.verifier = verifier
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _database(self, operation, *args):
        try:
            return operation(*args)
        except SQLAlchemyError:
            raise APIError(503) from None

    def _configured(self):
        if self.settings.google_client_id is None:
            raise APIError(503)

    def challenge(self, origin: str, client: str) -> Challenge:
        self._configured()
        self._database(self.store.rate_limit, "google_challenge", client, self.settings.auth_challenge_limit_per_minute)
        nonce = secrets.token_urlsafe(32)
        row = self._database(self.store.create_challenge, token_hash(nonce), origin)
        return Challenge(challenge_id=str(row.id), nonce=nonce, expires_at=timestamp(row.expires_at))

    def exchange(self, identifier, credential: str, origin: str, client: str) -> Session:
        self._configured()
        self._database(self.store.rate_limit, "google_exchange", client, self.settings.auth_exchange_limit_per_minute)
        challenge = self._database(self.store.challenge, identifier)
        if challenge is None or challenge["origin"] != origin:
            raise APIError(401, code="AUTH_CHALLENGE_INVALID")
        try:
            claims = self.verifier.verify(credential)
        except GoogleTokenInvalid:
            raise APIError(401, code="GOOGLE_TOKEN_INVALID") from None
        except Exception:
            raise APIError(503) from None
        try:
            identity = validate_claims(claims, self.settings.google_client_id, challenge["nonce_hash"], self.clock())
        except GoogleTokenInvalid:
            raise APIError(401, code="GOOGLE_TOKEN_INVALID") from None
        token = secrets.token_urlsafe(32)
        user, expires = self._database(
            self.store.exchange, identifier, challenge["nonce_hash"], origin, identity, token_hash(token),
        )
        return Session(access_token=token, expires_at=timestamp(expires), user=user)

    def me(self, token: str) -> Me:
        user, count = self._database(self.store.current_user, token_hash(token))
        return Me(user=user, owned_workspace_count=count)

    def logout(self, token: str):
        self._database(self.store.logout, token_hash(token))
