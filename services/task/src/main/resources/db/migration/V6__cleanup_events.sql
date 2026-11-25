CREATE TABLE task_service.processed_event (
 consumer_name text NOT NULL,
 event_id uuid NOT NULL,
 processed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (consumer_name,event_id)
);
