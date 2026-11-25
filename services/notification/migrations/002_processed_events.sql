-- Primary key rejects a second marker for the same event (deduplication).
CREATE TABLE notification_service.processed_events (
    consumer_name text NOT NULL,
    event_id uuid NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer_name, event_id)
);
