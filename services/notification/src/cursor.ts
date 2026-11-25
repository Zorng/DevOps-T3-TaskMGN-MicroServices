import { createHmac, timingSafeEqual } from 'node:crypto';
import { uuidPattern } from './http.js';
import type { Position } from './notifications.js';

export type Cursor = ReturnType<typeof createCursor>;

export class InvalidCursorError extends Error {}

const version = '1';
const timestampPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

export function createCursor(secret: string) {
  const sign = (data: Buffer) => createHmac('sha256', secret).update(data).digest();
  const scope = (recipientId: string, unreadOnly: boolean) => [version, recipientId, unreadOnly ? 'unread' : 'all'];

  return {
    encode(recipientId: string, unreadOnly: boolean, position: Position) {
      const data = Buffer.from([...scope(recipientId, unreadOnly), position.createdAt, position.id].join('|'));
      return `${data.toString('base64url')}.${sign(data).toString('base64url')}`;
    },

    decode(cursor: string, recipientId: string, unreadOnly: boolean): Position {
      const parts = cursor.split('.');
      if (parts.length !== 2) throw new InvalidCursorError();
      const data = Buffer.from(parts[0], 'base64url');
      const signature = Buffer.from(parts[1], 'base64url');
      const expected = sign(data);
      if (signature.length !== expected.length || !timingSafeEqual(signature, expected)) {
        throw new InvalidCursorError();
      }

      const fields = data.toString('utf8').split('|');
      const [createdAt, id] = fields.slice(3);
      if (fields.length !== 5 || fields.slice(0, 3).join('|') !== scope(recipientId, unreadOnly).join('|') ||
          !timestampPattern.test(createdAt) || !uuidPattern.test(id)) {
        throw new InvalidCursorError();
      }
      return { createdAt, id };
    },
  };
}
