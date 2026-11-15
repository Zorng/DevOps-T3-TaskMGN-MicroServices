"""Service-owned schema metadata; domain tables arrive with their features."""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, LargeBinary, MetaData, Table, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

SCHEMA = "identity_workspace"
SCHEMA_REVISION = "0002_google_sessions"
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

users = Table(
    "users", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("provider", Text, nullable=False),
    Column("provider_subject", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("email", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("owned_workspace_count", Integer, nullable=False, server_default=text("0")),
    UniqueConstraint("provider", "provider_subject", name="users_provider_subject"),
    CheckConstraint("provider = 'google'", name="users_google_provider"),
    CheckConstraint("length(provider_subject) BETWEEN 1 AND 255", name="users_subject_length"),
    CheckConstraint("length(display_name) BETWEEN 1 AND 200", name="users_display_name_length"),
    CheckConstraint("length(email) BETWEEN 1 AND 254", name="users_email_length"),
    CheckConstraint("owned_workspace_count BETWEEN 0 AND 10", name="users_owned_workspace_count"),
)
Index("users_email_lookup", func.lower(users.c.email))

auth_challenges = Table(
    "auth_challenges", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("nonce_hash", LargeBinary, nullable=False),
    Column("origin", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
    CheckConstraint("octet_length(nonce_hash) = 32", name="auth_challenge_nonce_hash"),
    CheckConstraint("expires_at = created_at + interval '5 minutes'", name="auth_challenge_lifetime"),
)
Index("auth_challenges_expiry", auth_challenges.c.expires_at)

sessions = Table(
    "sessions", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("token_hash", LargeBinary, nullable=False, unique=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    CheckConstraint("octet_length(token_hash) = 32", name="session_token_hash"),
    CheckConstraint("expires_at = created_at + interval '8 hours'", name="session_lifetime"),
)
Index("sessions_expiry", sessions.c.expires_at)
Index("sessions_user", sessions.c.user_id)

auth_rate_limits = Table(
    "auth_rate_limits", metadata,
    Column("bucket_hash", LargeBinary, primary_key=True),
    Column("window_started", DateTime(timezone=True), nullable=False),
    Column("attempts", Integer, nullable=False),
    CheckConstraint("octet_length(bucket_hash) = 32", name="auth_rate_bucket_hash"),
    CheckConstraint("attempts > 0", name="auth_rate_positive_attempts"),
)
