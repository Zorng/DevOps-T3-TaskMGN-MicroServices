"""Workspace ownership, memberships, invitation lifecycle storage and retries.

DDL is a frozen snapshot of the schema at this revision, independent of live models.
"""
from alembic import op

revision = "0003_workspace_management"
down_revision = "0002_google_sessions"
branch_labels = None
depends_on = None

DDL = (
    """CREATE TABLE identity_workspace.workspaces (
    id UUID NOT NULL,
    name TEXT NOT NULL,
    owner_id UUID NOT NULL,
    version BIGINT DEFAULT 1 NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT workspace_name_length CHECK (length(name) BETWEEN 1 AND 100 AND name = btrim(name)),
    CONSTRAINT workspace_version CHECK (version BETWEEN 1 AND 9007199254740991),
    FOREIGN KEY(owner_id) REFERENCES identity_workspace.users (id)
)""",
    """CREATE INDEX workspaces_active_owner ON identity_workspace.workspaces (owner_id) WHERE deleted_at IS NULL""",
    """CREATE TABLE identity_workspace.memberships (
    id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    user_id UUID NOT NULL,
    role TEXT NOT NULL,
    joined_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT membership_role CHECK (role IN ('owner', 'member')),
    FOREIGN KEY(workspace_id) REFERENCES identity_workspace.workspaces (id),
    FOREIGN KEY(user_id) REFERENCES identity_workspace.users (id)
)""",
    """CREATE UNIQUE INDEX memberships_active_owner ON identity_workspace.memberships (workspace_id) WHERE revoked_at IS NULL AND role = 'owner'""",
    """CREATE UNIQUE INDEX memberships_active_user_workspace ON identity_workspace.memberships (user_id, workspace_id) WHERE revoked_at IS NULL""",
    """CREATE INDEX memberships_workspace ON identity_workspace.memberships (workspace_id)""",
    """CREATE TABLE identity_workspace.invitations (
    id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    inviter_id UUID NOT NULL,
    recipient_id UUID NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT invitation_status CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled')),
    FOREIGN KEY(workspace_id) REFERENCES identity_workspace.workspaces (id),
    FOREIGN KEY(inviter_id) REFERENCES identity_workspace.users (id),
    FOREIGN KEY(recipient_id) REFERENCES identity_workspace.users (id)
)""",
    """CREATE UNIQUE INDEX invitations_pending_recipient ON identity_workspace.invitations (workspace_id, recipient_id) WHERE status = 'pending'""",
    """CREATE TABLE identity_workspace.idempotency_records (
    actor_id UUID NOT NULL,
    request_method TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    idempotency_key UUID NOT NULL,
    fingerprint BYTEA NOT NULL,
    resource_id UUID NOT NULL,
    status_code INTEGER NOT NULL,
    response_body JSONB NOT NULL,
    etag TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (actor_id, request_method, canonical_path, idempotency_key),
    CONSTRAINT idempotency_fingerprint CHECK (octet_length(fingerprint) = 32),
    CONSTRAINT idempotency_success CHECK (status_code BETWEEN 200 AND 299),
    CONSTRAINT idempotency_retention CHECK (expires_at >= created_at + interval '24 hours'),
    FOREIGN KEY(actor_id) REFERENCES identity_workspace.users (id)
)""",
    """CREATE INDEX idempotency_expiry ON identity_workspace.idempotency_records (expires_at)""",
    """CREATE TABLE identity_workspace.workspace_cursors (
    token_hash BYTEA NOT NULL,
    actor_id UUID NOT NULL,
    scope TEXT NOT NULL,
    last_created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (token_hash),
    CONSTRAINT workspace_cursor_hash CHECK (octet_length(token_hash) = 32),
    FOREIGN KEY(actor_id) REFERENCES identity_workspace.users (id)
)""",
    """CREATE INDEX workspace_cursors_expiry ON identity_workspace.workspace_cursors (expires_at)""",
)


def upgrade():
    for statement in DDL:
        op.execute(statement)
    lifecycle_guards()


def downgrade():
    op.execute("DROP TRIGGER outbox_immutable_event ON identity_workspace.outbox")
    op.execute("DROP FUNCTION identity_workspace.guard_outbox_event()")
    for name in ("workspace_cursors", "idempotency_records", "invitations", "memberships", "workspaces"):
        op.drop_table(name, schema="identity_workspace")
    op.execute("DROP FUNCTION identity_workspace.guard_active_workspace()")


def lifecycle_guards():
    # Future invitation/member writers must lock workspace before child rows.
    # The parent lock prevents inserts racing authoritative deletion, even for
    # direct SQL fixtures. Cancellation/revocation remains possible afterwards.
    op.execute("""
        CREATE FUNCTION identity_workspace.guard_active_workspace() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE active_owner uuid;
        BEGIN
            IF TG_TABLE_NAME = 'memberships' THEN
                IF NEW.revoked_at IS NULL THEN
                    SELECT owner_id INTO active_owner FROM identity_workspace.workspaces
                      WHERE id = NEW.workspace_id AND deleted_at IS NULL FOR SHARE;
                    IF active_owner IS NULL OR (NEW.role = 'owner' AND NEW.user_id <> active_owner) THEN
                        RAISE EXCEPTION 'Invalid active workspace membership' USING ERRCODE = '23514';
                    END IF;
                END IF;
            ELSIF TG_TABLE_NAME = 'invitations' THEN
                IF NEW.status IN ('pending', 'accepted') THEN
                    PERFORM id FROM identity_workspace.workspaces
                      WHERE id = NEW.workspace_id AND deleted_at IS NULL FOR SHARE;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'Workspace unavailable' USING ERRCODE = '23514';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    for table in ("memberships", "invitations"):
        op.execute(f"CREATE TRIGGER {table}_active_workspace BEFORE INSERT OR UPDATE ON "
                   f"identity_workspace.{table} FOR EACH ROW "
                   "EXECUTE FUNCTION identity_workspace.guard_active_workspace()")
    op.execute("""
        CREATE FUNCTION identity_workspace.guard_outbox_event() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.event_id, NEW.event_type, NEW.payload, NEW.created_at)
                IS DISTINCT FROM ROW(OLD.event_id, OLD.event_type, OLD.payload, OLD.created_at) THEN
                RAISE EXCEPTION 'Outbox event is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER outbox_immutable_event BEFORE UPDATE ON identity_workspace.outbox "
               "FOR EACH ROW EXECUTE FUNCTION identity_workspace.guard_outbox_event()")
