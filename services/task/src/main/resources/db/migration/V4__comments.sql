CREATE TABLE task_service.comment (
 id uuid PRIMARY KEY, workspace_id uuid NOT NULL, task_id uuid NOT NULL REFERENCES task_service.task(id),
 body text NOT NULL, author_id uuid NOT NULL, author_name text NOT NULL, created_at timestamptz NOT NULL
);
CREATE INDEX comment_page_idx ON task_service.comment(workspace_id,task_id,created_at,id);
CREATE TABLE task_service.comment_mention (
 comment_id uuid NOT NULL REFERENCES task_service.comment(id), position integer NOT NULL,
 user_id uuid NOT NULL, display_name text NOT NULL, membership_id uuid NOT NULL,
 start_offset integer NOT NULL, length integer NOT NULL, PRIMARY KEY(comment_id,position)
);
CREATE TABLE task_service.comment_create_key (
 actor_id uuid NOT NULL, path text NOT NULL, key uuid NOT NULL, fingerprint text NOT NULL,
 comment_id uuid NOT NULL REFERENCES task_service.comment(id), response jsonb NOT NULL, expires_at timestamptz NOT NULL,
 PRIMARY KEY(actor_id,path,key)
);
