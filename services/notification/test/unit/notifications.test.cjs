const assert = require('node:assert/strict');
const { test } = require('node:test');
const { countUnread, listNotifications, markRead, toNotification } = require('../../dist/notifications.js');

const recipient = '6f1c3a52-0e0b-4c1e-9d55-6a2b2c5f6a01';

function row(id, overrides = {}) {
  return {
    id,
    event_id: '00000000-0000-4000-8000-000000000102',
    type: 'assignment',
    actor_id: '00000000-0000-4000-8000-000000000001',
    actor_display_name: 'Example Owner',
    workspace_id: '00000000-0000-4000-8000-000000000003',
    invitation_id: null,
    task_id: '00000000-0000-4000-8000-000000000006',
    comment_id: null,
    occurred_at: new Date('2026-11-14T10:00:00Z'),
    created_at: new Date('2026-11-14T10:00:00.250Z'),
    read_at: null,
    ...overrides,
  };
}

function fakePool(t, rows) {
  return { query: t.mock.fn(async () => ({ rows })) };
}

test('a row maps to the public notification shape', () => {
  assert.deepEqual(toNotification(row('00000000-0000-4000-8000-000000000011', {
    read_at: new Date('2026-11-14T10:01:00Z'),
  })), {
    id: '00000000-0000-4000-8000-000000000011',
    event_id: '00000000-0000-4000-8000-000000000102',
    type: 'assignment',
    actor: { id: '00000000-0000-4000-8000-000000000001', display_name: 'Example Owner' },
    workspace_id: '00000000-0000-4000-8000-000000000003',
    invitation_id: null,
    task_id: '00000000-0000-4000-8000-000000000006',
    comment_id: null,
    occurred_at: '2026-11-14T10:00:00.000Z',
    created_at: '2026-11-14T10:00:00.250Z',
    read_at: '2026-11-14T10:01:00.000Z',
  });
});

test('listing is scoped to the recipient, ordered newest first and fetches one extra row', async (t) => {
  const pool = fakePool(t, [row('a'), row('b'), row('c')]);
  const after = { createdAt: '2026-11-14T10:00:00.250Z', id: 'd' };
  const page = await listNotifications(pool, recipient, { limit: 2, unreadOnly: true, after });
  const [sql, params] = pool.query.mock.calls[0].arguments;
  assert.match(sql, /WHERE recipient_id = \$1/);
  assert.match(sql, /ORDER BY created_at DESC, id DESC/);
  assert.deepEqual(params, [recipient, true, after.createdAt, after.id, 3]);
  assert.deepEqual(page.items.map((item) => item.id), ['a', 'b']);
  assert.deepEqual(page.next, { createdAt: '2026-11-14T10:00:00.250Z', id: 'b' });

  const last = await listNotifications(fakePool(t, [row('a')]), recipient, { limit: 2, unreadOnly: false });
  assert.equal(last.next, undefined);
});

test('the unread count is scoped to the recipient and unread rows', async (t) => {
  const pool = fakePool(t, [{ count: 4 }]);
  assert.equal(await countUnread(pool, recipient), 4);
  const [sql, params] = pool.query.mock.calls[0].arguments;
  assert.match(sql, /recipient_id = \$1 AND read_at IS NULL/);
  assert.deepEqual(params, [recipient]);
});

test('mark-as-read preserves the first read time and needs the recipient to match', async (t) => {
  const pool = fakePool(t, [row('a', { read_at: new Date('2026-11-14T10:01:00Z') })]);
  assert.equal((await markRead(pool, recipient, 'a')).read_at, '2026-11-14T10:01:00.000Z');
  const [sql, params] = pool.query.mock.calls[0].arguments;
  assert.match(sql, /COALESCE\(read_at,/);
  assert.match(sql, /WHERE id = \$1 AND recipient_id = \$2/);
  assert.deepEqual(params, ['a', recipient]);

  assert.equal(await markRead(fakePool(t, []), recipient, 'missing'), undefined);
});
