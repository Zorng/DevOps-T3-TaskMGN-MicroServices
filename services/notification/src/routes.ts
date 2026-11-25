import { Router, type Request, type Response } from 'express';
import type { Pool } from 'pg';
import { createCursor, type Cursor } from './cursor.js';
import { sendError, uuidPattern } from './http.js';
import { countUnread, listNotifications, markRead, type Position } from './notifications.js';

const minSecretBytes = 32;

export function createNotificationRoutes(pool: Pool | undefined, cursorSecret = '') {
  const cursor = Buffer.byteLength(cursorSecret) >= minSecretBytes ? createCursor(cursorSecret) : undefined;
  const routes = Router();

  routes.get('/', async (request, response) => {
    const query = parseListQuery(request);
    if (!query) return malformed(response, 'Use only limit (1-100), cursor and unread_only (true or false).');
    if (!pool || !cursor) return unavailable(response);

    const recipientId: string = response.locals.recipientId;
    const after = query.cursor === undefined ? undefined : decode(cursor, query.cursor, recipientId, query.unreadOnly);
    if (after === null) return sendError(response, 400, 'INVALID_CURSOR', 'The cursor is not valid for this list.');

    const page = await listNotifications(pool, recipientId, { ...query, after });
    response.json({
      items: page.items,
      next_cursor: page.next ? cursor.encode(recipientId, query.unreadOnly, page.next) : null,
    });
  });

  routes.get('/unread-count', async (request, response) => {
    if (hasInput(request)) return malformed(response, 'This endpoint accepts no body or query parameters.');
    if (!pool) return unavailable(response);
    response.json({ count: await countUnread(pool, response.locals.recipientId) });
  });

  routes.post('/:notification_id/read', async (request, response) => {
    if (hasInput(request)) return malformed(response, 'This endpoint accepts no body or query parameters.');
    const id = String(request.params.notification_id);
    if (!uuidPattern.test(id)) return malformed(response, 'notification_id must be a UUID.');
    if (!pool) return unavailable(response);

    const notification = await markRead(pool, response.locals.recipientId, id);
    if (!notification) return sendError(response, 404, 'NOT_FOUND', 'Notification not found.');
    response.json(notification);
  });

  return routes;
}

function parseListQuery(request: Request) {
  if (request.body?.length) return;
  const { limit = '20', cursor, unread_only = 'false', ...unsupported } = request.query;
  if (Object.keys(unsupported).length) return;
  if (typeof limit !== 'string' || !/^[1-9][0-9]{0,2}$/.test(limit) || Number(limit) > 100) return;
  if (unread_only !== 'true' && unread_only !== 'false') return;
  if (cursor !== undefined && (typeof cursor !== 'string' || cursor.length < 1 || cursor.length > 1024)) return;
  return { limit: Number(limit), unreadOnly: unread_only === 'true', cursor };
}

function decode(cursor: Cursor, value: string, recipientId: string, unreadOnly: boolean): Position | null {
  try {
    return cursor.decode(value, recipientId, unreadOnly);
  } catch {
    return null;
  }
}

function hasInput(request: Request) {
  return Boolean(request.body?.length) || Object.keys(request.query).length > 0;
}

function malformed(response: Response, message: string) {
  sendError(response, 400, 'MALFORMED_REQUEST', message);
}

function unavailable(response: Response) {
  response.set('Retry-After', '1');
  sendError(response, 503, 'DEPENDENCY_UNAVAILABLE', 'Notifications are temporarily unavailable.');
}
