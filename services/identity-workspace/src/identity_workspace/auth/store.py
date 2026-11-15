"""Service-owned authentication persistence; no provider HTTP inside transactions."""

from datetime import timedelta
import hashlib
import math
from uuid import uuid4

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from identity_workspace.auth.models import public_user
from identity_workspace.errors import APIError
from identity_workspace.persistence import auth_challenges, auth_rate_limits, sessions, users


def database_now(session):
    return session.scalar(select(func.date_trunc("milliseconds", func.clock_timestamp())))


class AuthStore:
    def __init__(self, database):
        self.database = database

    def rate_limit(self, operation: str, client: str, limit: int):
        bucket = hashlib.sha256(f"{operation}:{client}".encode("utf-8")).digest()
        with self.database.transaction() as session:
            now = database_now(session)
            expired = auth_rate_limits.c.window_started <= now - timedelta(minutes=1)
            statement = insert(auth_rate_limits).values(bucket_hash=bucket, window_started=now, attempts=1)
            statement = statement.on_conflict_do_update(
                index_elements=[auth_rate_limits.c.bucket_hash],
                set_={
                    "window_started": case((expired, now), else_=auth_rate_limits.c.window_started),
                    "attempts": case((expired, 1), else_=func.least(auth_rate_limits.c.attempts + 1, limit + 1)),
                },
            ).returning(auth_rate_limits.c.window_started, auth_rate_limits.c.attempts)
            row = session.execute(statement).one()
            observed = database_now(session)
            retry = max(1, min(60, math.ceil((row.window_started + timedelta(minutes=1) - observed).total_seconds())))
            limited = row.attempts > limit
        # Commit the attempt independently, including unsuccessful exchanges.
        if limited:
            raise APIError(429, retry_after=retry)

    def create_challenge(self, nonce_hash: bytes, origin: str):
        with self.database.transaction() as session:
            now = database_now(session)
            return session.execute(auth_challenges.insert().values(
                id=uuid4(), nonce_hash=nonce_hash, origin=origin, created_at=now,
                expires_at=now + timedelta(minutes=5),
            ).returning(auth_challenges.c.id, auth_challenges.c.expires_at)).one()

    def challenge(self, identifier):
        with self.database.transaction() as session:
            return session.execute(select(auth_challenges).where(
                auth_challenges.c.id == identifier,
                auth_challenges.c.consumed_at.is_(None),
                auth_challenges.c.expires_at > func.clock_timestamp(),
            )).mappings().one_or_none()

    def exchange(self, identifier, nonce_hash: bytes, origin: str, identity, token_hash: bytes):
        with self.database.transaction() as session:
            consumed = session.execute(update(auth_challenges).where(
                auth_challenges.c.id == identifier,
                auth_challenges.c.nonce_hash == nonce_hash,
                auth_challenges.c.origin == origin,
                auth_challenges.c.consumed_at.is_(None),
                auth_challenges.c.expires_at > func.clock_timestamp(),
            ).values(consumed_at=func.clock_timestamp()).returning(auth_challenges.c.id)).scalar_one_or_none()
            if consumed is None:
                raise APIError(401, code="AUTH_CHALLENGE_INVALID")
            now = database_now(session)
            statement = insert(users).values(
                id=uuid4(), provider="google", provider_subject=identity.subject,
                display_name=identity.display_name, email=identity.email, created_at=now,
            )
            row = session.execute(statement.on_conflict_do_update(
                constraint="users_provider_subject",
                set_={"display_name": statement.excluded.display_name, "email": statement.excluded.email},
            ).returning(users)).mappings().one()
            expires = now + timedelta(hours=8)
            session.execute(sessions.insert().values(
                id=uuid4(), user_id=row["id"], token_hash=token_hash,
                created_at=now, expires_at=expires,
            ))
            user = public_user(row)
        return user, expires

    def current_user(self, token_hash: bytes):
        with self.database.transaction() as session:
            row = session.execute(select(users).join(sessions, sessions.c.user_id == users.c.id).where(
                sessions.c.token_hash == token_hash,
                sessions.c.revoked_at.is_(None),
                sessions.c.expires_at > func.clock_timestamp(),
            )).mappings().one_or_none()
            if row is None:
                raise APIError(401, code="SESSION_INVALID")
            return public_user(row), row["owned_workspace_count"]

    def logout(self, token_hash: bytes):
        with self.database.transaction() as session:
            identifier = session.execute(update(sessions).where(
                sessions.c.token_hash == token_hash,
                sessions.c.revoked_at.is_(None),
                sessions.c.expires_at > func.clock_timestamp(),
            ).values(revoked_at=func.clock_timestamp()).returning(sessions.c.id)).scalar_one_or_none()
            if identifier is None:
                raise APIError(401, code="SESSION_INVALID")

    def cleanup(self, limit=1000):
        counts = {}
        with self.database.transaction() as session:
            for table, predicate, key in (
                (sessions, (sessions.c.expires_at <= func.clock_timestamp()) | sessions.c.revoked_at.is_not(None), sessions.c.id),
                (auth_challenges, auth_challenges.c.expires_at <= func.clock_timestamp(), auth_challenges.c.id),
                (auth_rate_limits, auth_rate_limits.c.window_started < func.clock_timestamp() - timedelta(hours=1), auth_rate_limits.c.bucket_hash),
            ):
                batch = select(key).where(predicate).limit(limit).with_for_update(skip_locked=True)
                counts[table.name] = session.execute(delete(table).where(key.in_(batch))).rowcount
        return counts
