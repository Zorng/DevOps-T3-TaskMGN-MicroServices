import type { Pool } from 'pg';

export type Position = { createdAt: string; id: string };

type Row = {
  id: string;
  event_id: string;
  type: 'invitation' | 'assignment' | 'mention';
  actor_id: string;
  actor_display_name: string;
  workspace_id: string;
  invitation_id: string | null;
  task_id: string | null;
  comment_id: string | null;
  occurred_at: Date;
  created_at: Date;
  read_at: Date | null;
};

const columns = `id, event_id, type, actor_id, actor_display_name, workspace_id,
  invitation_id, task_id, comment_id, occurred_at, created_at, read_at`;

export async function listNotifications(
  pool: Pool,
  recipientId: string,
  options: { limit: number; unreadOnly: boolean; after?: Position },
) {
  const { rows } = await pool.query<Row>(
    `SELECT ${columns} FROM notification_service.notifications
      WHERE recipient_id = $1
        AND (NOT $2::boolean OR read_at IS NULL)
        AND ($3::timestamptz IS NULL OR (created_at, id) < ($3::timestamptz, $4::uuid))
      ORDER BY created_at DESC, id DESC
      LIMIT $5`,
    [recipientId, options.unreadOnly, options.after?.createdAt ?? null, options.after?.id ?? null, options.limit + 1],
  );
  const items = rows.slice(0, options.limit).map(toNotification);
  const last = rows.length > options.limit ? items.at(-1) : undefined;
  return { items, next: last && { createdAt: last.created_at, id: last.id } };
}

export async function countUnread(pool: Pool, recipientId: string) {
  const { rows } = await pool.query<{ count: number }>(
    `SELECT count(*)::integer AS count FROM notification_service.notifications
      WHERE recipient_id = $1 AND read_at IS NULL`,
    [recipientId],
  );
  return rows[0].count;
}

export async function markRead(pool: Pool, recipientId: string, notificationId: string) {
  const { rows } = await pool.query<Row>(
    `UPDATE notification_service.notifications
      SET read_at = COALESCE(read_at, date_trunc('milliseconds', now()))
      WHERE id = $1 AND recipient_id = $2
      RETURNING ${columns}`,
    [notificationId, recipientId],
  );
  return rows[0] && toNotification(rows[0]);
}

export function toNotification(row: Row) {
  return {
    id: row.id,
    event_id: row.event_id,
    type: row.type,
    actor: { id: row.actor_id, display_name: row.actor_display_name },
    workspace_id: row.workspace_id,
    invitation_id: row.invitation_id,
    task_id: row.task_id,
    comment_id: row.comment_id,
    occurred_at: row.occurred_at.toISOString(),
    created_at: row.created_at.toISOString(),
    read_at: row.read_at?.toISOString() ?? null,
  };
}
