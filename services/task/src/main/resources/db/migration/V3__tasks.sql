CREATE TABLE task_service.workspace_tombstone (workspace_id uuid PRIMARY KEY);
CREATE TABLE task_service.membership_tombstone (workspace_id uuid NOT NULL, membership_id uuid NOT NULL, PRIMARY KEY (workspace_id, membership_id));
CREATE TABLE task_service.task_number (workspace_id uuid PRIMARY KEY, last_number bigint NOT NULL CHECK (last_number BETWEEN 1 AND 9007199254740991));
CREATE TABLE task_service.task (
 id uuid PRIMARY KEY, workspace_id uuid NOT NULL, board_id uuid NOT NULL REFERENCES task_service.board(id),
 number bigint NOT NULL CHECK (number BETWEEN 1 AND 9007199254740991), title text NOT NULL, description text NOT NULL,
 creator_id uuid NOT NULL, creator_name text NOT NULL, assignee_id uuid, assignee_name text, assignee_membership_id uuid,
 assignee_workspace_version bigint, start_date date, due_date date, version bigint NOT NULL CHECK (version BETWEEN 1 AND 9007199254740991),
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, deleted_at timestamptz,
 UNIQUE (workspace_id, number),
 CHECK ((assignee_id IS NULL AND assignee_name IS NULL AND assignee_membership_id IS NULL AND assignee_workspace_version IS NULL)
     OR (assignee_id IS NOT NULL AND assignee_name IS NOT NULL AND assignee_membership_id IS NOT NULL AND assignee_workspace_version IS NOT NULL)),
 CHECK (start_date IS NULL OR due_date IS NULL OR start_date <= due_date)
);
CREATE INDEX task_active_page_idx ON task_service.task(workspace_id, created_at DESC, id DESC) WHERE deleted_at IS NULL;
CREATE TABLE task_service.task_create_key (
 actor_id uuid NOT NULL, path text NOT NULL, key uuid NOT NULL, fingerprint text NOT NULL, task_id uuid NOT NULL REFERENCES task_service.task(id),
 response jsonb NOT NULL, expires_at timestamptz NOT NULL, PRIMARY KEY(actor_id,path,key)
);
CREATE TABLE task_service.outbox (
 id uuid PRIMARY KEY, event_type text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL, published_at timestamptz
);
