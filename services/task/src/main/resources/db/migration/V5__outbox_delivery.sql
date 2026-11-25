ALTER TABLE task_service.outbox ADD COLUMN attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0);
ALTER TABLE task_service.outbox ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX outbox_pending_due_idx ON task_service.outbox(next_attempt_at,created_at,id) WHERE published_at IS NULL;
