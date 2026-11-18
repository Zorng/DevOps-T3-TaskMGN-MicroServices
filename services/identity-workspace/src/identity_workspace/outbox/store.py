"""Short PostgreSQL leases with fencing; no transaction is held during broker I/O."""

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, update

from identity_workspace.auth.store import database_now
from identity_workspace.persistence import outbox

LEASE_SECONDS = 120


@dataclass(frozen=True)
class Delivery:
    event_id: UUID
    attempt: int
    payload: dict = field(repr=False)


class OutboxStore:
    def __init__(self, database):
        self.database = database

    def claim(self) -> Delivery | None:
        with self.database.transaction() as session:
            now = database_now(session)
            row = session.execute(select(outbox).where(
                outbox.c.published_at.is_(None), outbox.c.next_attempt_at <= now,
            ).order_by(outbox.c.next_attempt_at, outbox.c.created_at, outbox.c.event_id)
                .with_for_update(skip_locked=True).limit(1)).mappings().first()
            if row is None:
                return None
            attempt = row["attempts"] + 1
            session.execute(update(outbox).where(outbox.c.event_id == row["event_id"]).values(
                attempts=attempt, next_attempt_at=now + timedelta(seconds=LEASE_SECONDS),
            ))
            return Delivery(row["event_id"], attempt, row["payload"])

    def complete(self, delivery: Delivery, *, delay: float | None = None) -> bool:
        with self.database.transaction() as session:
            now = database_now(session)
            values = {"published_at": now} if delay is None else {"next_attempt_at": now + timedelta(seconds=delay)}
            result = session.execute(update(outbox).where(
                outbox.c.event_id == delivery.event_id, outbox.c.attempts == delivery.attempt,
                outbox.c.published_at.is_(None),
            ).values(**values))
            return result.rowcount == 1

    def stats(self) -> tuple[int, float]:
        with self.database.transaction() as session:
            count, oldest = session.execute(select(func.count(), func.min(outbox.c.created_at))
                .where(outbox.c.published_at.is_(None))).one()
            age = max(0.0, (database_now(session) - oldest).total_seconds()) if oldest else 0.0
            return count, age

    def purge_confirmed(self, *, days: int = 7, limit: int = 1000) -> int:
        if type(days) is not int or days < 7 or type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("Retention must be at least seven days; batch size 1–10000.")
        with self.database.transaction() as session:
            cutoff = database_now(session) - timedelta(days=days)
            identifiers = select(outbox.c.event_id).where(outbox.c.published_at < cutoff).order_by(
                outbox.c.published_at, outbox.c.event_id).limit(limit).with_for_update(skip_locked=True)
            return session.execute(delete(outbox).where(outbox.c.event_id.in_(identifiers))).rowcount
