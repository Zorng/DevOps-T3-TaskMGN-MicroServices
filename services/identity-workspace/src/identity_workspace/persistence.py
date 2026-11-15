"""Service-owned schema metadata; domain tables arrive with their features."""

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, MetaData, Table, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

SCHEMA = "identity_workspace"
SCHEMA_REVISION = "0001_identity_foundation"
metadata = MetaData(schema=SCHEMA)

outbox = Table(
    "outbox",
    metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("event_type", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("published_at", DateTime(timezone=True)),
    CheckConstraint("attempts >= 0", name="outbox_nonnegative_attempts"),
    CheckConstraint(
        "event_type IN ('invitation.created.v1', 'workspace.member_removed.v1', 'workspace.deleted.v1')",
        name="outbox_identity_event_type",
    ),
    CheckConstraint(
        "(jsonb_typeof(payload) = 'object' AND payload ? 'id' AND payload ? 'type' "
        "AND payload ? 'producer' AND payload ? 'schema_version' "
        "AND payload->>'id' = event_id::text AND payload->>'type' = event_type "
        "AND payload->>'producer' = 'identity-workspace' "
        "AND payload->>'schema_version' = '1') IS TRUE",
        name="outbox_envelope_identity",
    ),
    CheckConstraint("octet_length(payload::text) <= 65536", name="outbox_payload_size"),
)

Index(
    "outbox_pending",
    outbox.c.next_attempt_at,
    outbox.c.created_at,
    postgresql_where=outbox.c.published_at.is_(None),
)
