-- Millisecond timestamps keep API values and cursor positions identical.
ALTER TABLE notification_service.notifications
    ALTER COLUMN created_at SET DEFAULT date_trunc('milliseconds', now());

UPDATE notification_service.notifications
    SET created_at = date_trunc('milliseconds', created_at),
        read_at = date_trunc('milliseconds', read_at);

CREATE INDEX notifications_recipient_page
    ON notification_service.notifications (recipient_id, created_at DESC, id DESC);

CREATE INDEX notifications_recipient_unread
    ON notification_service.notifications (recipient_id, created_at DESC, id DESC)
    WHERE read_at IS NULL;
