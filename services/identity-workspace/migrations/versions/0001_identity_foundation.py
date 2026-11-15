"""Create the Identity schema and pending-event storage.

Revision ID: 0001_identity_foundation
Revises: None
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_identity_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.schema.CreateSchema("identity_workspace"))
    op.create_table(
        "outbox",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("attempts >= 0", name="outbox_nonnegative_attempts"),
        sa.CheckConstraint(
            "event_type IN ('invitation.created.v1', 'workspace.member_removed.v1', 'workspace.deleted.v1')",
            name="outbox_identity_event_type",
        ),
        sa.CheckConstraint(
            "(jsonb_typeof(payload) = 'object' AND payload ? 'id' AND payload ? 'type' "
            "AND payload ? 'producer' AND payload ? 'schema_version' "
            "AND payload->>'id' = event_id::text AND payload->>'type' = event_type "
            "AND payload->>'producer' = 'identity-workspace' "
            "AND payload->>'schema_version' = '1') IS TRUE",
            name="outbox_envelope_identity",
        ),
        sa.CheckConstraint("octet_length(payload::text) <= 65536", name="outbox_payload_size"),
        schema="identity_workspace",
    )
    op.create_index(
        "outbox_pending",
        "outbox",
        ["next_attempt_at", "created_at"],
        schema="identity_workspace",
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade():
    op.drop_table("outbox", schema="identity_workspace")
    op.execute(sa.schema.DropSchema("identity_workspace"))
