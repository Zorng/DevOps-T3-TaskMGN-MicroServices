CREATE SCHEMA notification_service;

CREATE TABLE notification_service.notifications (
    id uuid PRIMARY KEY,
    event_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    type text NOT NULL CHECK (type IN ('invitation', 'assignment', 'mention')),
    actor_id uuid NOT NULL,
    actor_display_name text NOT NULL,
    workspace_id uuid NOT NULL,
    invitation_id uuid,
    task_id uuid,
    comment_id uuid,
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    read_at timestamptz,
    UNIQUE (event_id, recipient_id)
);
