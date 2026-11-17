"""Make membership generations immutable and revocation terminal."""

from alembic import op

revision = "0004_membership_generations"
down_revision = "0003_workspace_management"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION identity_workspace.guard_membership_generation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.id, NEW.workspace_id, NEW.user_id, NEW.role, NEW.joined_at)
                IS DISTINCT FROM ROW(OLD.id, OLD.workspace_id, OLD.user_id, OLD.role, OLD.joined_at) THEN
                RAISE EXCEPTION 'Membership generation is immutable' USING ERRCODE = '23514';
            END IF;
            IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
                RAISE EXCEPTION 'Membership revocation is terminal' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER memberships_immutable_generation BEFORE UPDATE ON identity_workspace.memberships "
               "FOR EACH ROW EXECUTE FUNCTION identity_workspace.guard_membership_generation()")


def downgrade():
    op.execute("DROP TRIGGER memberships_immutable_generation ON identity_workspace.memberships")
    op.execute("DROP FUNCTION identity_workspace.guard_membership_generation()")
