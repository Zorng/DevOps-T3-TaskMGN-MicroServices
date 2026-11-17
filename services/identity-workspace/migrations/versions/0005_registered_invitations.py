"""Invitation cursor storage, terminal history protection and optional create ETags."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_registered_invitations"
down_revision = "0004_membership_generations"
branch_labels = None
depends_on = None
SCHEMA = "identity_workspace"


def upgrade():
    op.alter_column("idempotency_records", "etag", existing_type=sa.Text(), nullable=True, schema=SCHEMA)
    op.create_table(
        "invitation_cursors",
        sa.Column("token_hash", sa.LargeBinary(), primary_key=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.users.id"), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("last_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="invitation_cursor_hash"),
        schema=SCHEMA,
    )
    op.create_index("invitation_cursors_expiry", "invitation_cursors", ["expires_at"], schema=SCHEMA)
    for kind in ("workspace", "recipient"):
        op.create_index(f"invitations_{kind}_order", "invitations",
                        [f"{kind}_id", sa.text("created_at DESC"), sa.text("id DESC")], schema=SCHEMA)
    op.execute("""
        CREATE FUNCTION identity_workspace.guard_invitation_history() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.id, NEW.workspace_id, NEW.inviter_id, NEW.recipient_id, NEW.created_at)
                IS DISTINCT FROM ROW(OLD.id, OLD.workspace_id, OLD.inviter_id, OLD.recipient_id, OLD.created_at) THEN
                RAISE EXCEPTION 'Invitation identity is immutable' USING ERRCODE = '23514';
            END IF;
            IF OLD.status <> 'pending' AND ROW(NEW.status, NEW.updated_at)
                IS DISTINCT FROM ROW(OLD.status, OLD.updated_at) THEN
                RAISE EXCEPTION 'Invitation outcome is terminal' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER invitations_immutable_history BEFORE UPDATE ON identity_workspace.invitations "
               "FOR EACH ROW EXECUTE FUNCTION identity_workspace.guard_invitation_history()")


def downgrade():
    op.execute("DROP TRIGGER invitations_immutable_history ON identity_workspace.invitations")
    op.execute("DROP FUNCTION identity_workspace.guard_invitation_history()")
    for kind in ("workspace", "recipient"):
        op.drop_index(f"invitations_{kind}_order", table_name="invitations", schema=SCHEMA)
    op.drop_table("invitation_cursors", schema=SCHEMA)
    # Preserve all retry records across a downgrade. The previous service has no
    # invitation endpoints and never uses their absent ETag as a response header.
    op.execute("UPDATE identity_workspace.idempotency_records SET etag = '' WHERE etag IS NULL")
    op.alter_column("idempotency_records", "etag", existing_type=sa.Text(), nullable=False, schema=SCHEMA)
