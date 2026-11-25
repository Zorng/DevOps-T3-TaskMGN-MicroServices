import { randomUUID } from 'node:crypto';
import type { Pool, PoolClient } from 'pg';
import { parseEvent, type NotificationRecord } from './events.js';

export const consumerName = 'notifications.v1';

export type ProcessResult = 'created' | 'suppressed' | 'duplicate';

// Throws InvalidEventError for permanent rejections; any other error is retryable.
export async function processEvent(pool: Pool, message: Buffer | string): Promise<ProcessResult> {
  const record = parseEvent(message);
  const client = await pool.connect();
  try {
    const result = await apply(client, record);
    client.release();
    return result;
  } catch (error) {
    client.release(true);
    throw error;
  }
}

async function apply(client: PoolClient, record: NotificationRecord): Promise<ProcessResult> {
  await client.query('BEGIN');
  const marker = await client.query(
    `INSERT INTO notification_service.processed_events (consumer_name, event_id)
      VALUES ($1, $2) ON CONFLICT DO NOTHING`,
    [consumerName, record.eventId],
  );
  let result: ProcessResult = 'duplicate';
  if (marker.rowCount === 1) {
    result = record.actorId === record.recipientId ? 'suppressed' : await insertNotification(client, record);
  }
  await client.query('COMMIT');
  return result;
}

async function insertNotification(client: PoolClient, record: NotificationRecord): Promise<ProcessResult> {
  const inserted = await client.query(
    `INSERT INTO notification_service.notifications
      (id, event_id, recipient_id, type, actor_id, actor_display_name,
       workspace_id, invitation_id, task_id, comment_id, occurred_at)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
      ON CONFLICT (event_id, recipient_id) DO NOTHING`,
    [
      randomUUID(), record.eventId, record.recipientId, record.type, record.actorId, record.actorDisplayName,
      record.workspaceId, record.invitationId, record.taskId, record.commentId, record.occurredAt,
    ],
  );
  return inserted.rowCount === 1 ? 'created' : 'duplicate';
}
