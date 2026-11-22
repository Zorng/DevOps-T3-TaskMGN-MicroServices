CREATE TABLE task_service.board (
    id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL,
    name text NOT NULL,
    version bigint NOT NULL DEFAULT 1 CHECK (version BETWEEN 1 AND 9007199254740991),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz
);
CREATE INDEX board_active_workspace_idx ON task_service.board (workspace_id, created_at, id) WHERE deleted_at IS NULL;
CREATE TABLE task_service.board_create_key (
    actor_id uuid NOT NULL,
    path text NOT NULL,
    key uuid NOT NULL,
    fingerprint text NOT NULL,
    board_id uuid NOT NULL REFERENCES task_service.board(id),
    original_name text NOT NULL,
    original_created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (actor_id, path, key)
);
