"""Add Google identities, single-use challenges, hashed sessions, and IP limits."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_google_sessions"
down_revision = "0001_identity_foundation"
branch_labels = None
depends_on = None
SCHEMA = "identity_workspace"


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_subject", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owned_workspace_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("provider", "provider_subject", name="users_provider_subject"),
        sa.CheckConstraint("provider = 'google'", name="users_google_provider"),
        sa.CheckConstraint("length(provider_subject) BETWEEN 1 AND 255", name="users_subject_length"),
        sa.CheckConstraint("length(display_name) BETWEEN 1 AND 200", name="users_display_name_length"),
        sa.CheckConstraint("length(email) BETWEEN 1 AND 254", name="users_email_length"),
        sa.CheckConstraint("owned_workspace_count BETWEEN 0 AND 10", name="users_owned_workspace_count"),
        schema=SCHEMA,
    )
    op.create_index("users_email_lookup", "users", [sa.text("lower(email)")], schema=SCHEMA)
    op.create_table(
        "auth_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nonce_hash", sa.LargeBinary(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("octet_length(nonce_hash) = 32", name="auth_challenge_nonce_hash"),
        sa.CheckConstraint("expires_at = created_at + interval '5 minutes'", name="auth_challenge_lifetime"),
        schema=SCHEMA,
    )
    op.create_index("auth_challenges_expiry", "auth_challenges", ["expires_at"], schema=SCHEMA)
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="session_token_hash"),
        sa.CheckConstraint("expires_at = created_at + interval '8 hours'", name="session_lifetime"),
        schema=SCHEMA,
    )
    op.create_index("sessions_expiry", "sessions", ["expires_at"], schema=SCHEMA)
    op.create_index("sessions_user", "sessions", ["user_id"], schema=SCHEMA)
    op.create_table(
        "auth_rate_limits",
        sa.Column("bucket_hash", sa.LargeBinary(), primary_key=True),
        sa.Column("window_started", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.CheckConstraint("octet_length(bucket_hash) = 32", name="auth_rate_bucket_hash"),
        sa.CheckConstraint("attempts > 0", name="auth_rate_positive_attempts"),
        schema=SCHEMA,
    )


def downgrade():
    for name in ("auth_rate_limits", "sessions", "auth_challenges", "users"):
        op.drop_table(name, schema=SCHEMA)
