const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { test } = require('node:test');
const { createApp } = require('../../dist/app.js');
const { createPool } = require('../../dist/dependencies.js');
const { processEvent } = require('../../dist/processing.js');
const { createNotificationRoutes } = require('../../dist/routes.js');
const { assertResponse } = require('../contract.cjs');
const { freshEvent } = require('../events.cjs');
const { listen } = require('../http.cjs');

const secret = 'integration-cursor-secret-32-bytes!!';

async function setup(t) {
  assert.ok(process.env.DATABASE_URL, 'Set DATABASE_URL to a migrated disposable test database');
  const pool = createPool(process.env.DATABASE_URL);
  t.after(() => pool.end());
  let recipientId;
  const app = createApp(async () => {}, {
    introspect: async () => ({ kind: 'verified', recipientId }),
    routes: createNotificationRoutes(pool, secret),
  });
  const base = (await listen(t, app)) + '/api/v1/notifications';
  const as = (recipient) => {
    recipientId = recipient;
    return { headers: { Authorization: 'Bearer controlled-session' } };
  };
  return { pool, base, as };
}

async function seed(pool, recipient, count, createdAt) {
  const ids = [];
  for (let index = 0; index < count; index++) {
    const event = freshEvent(index % 2 ? 'task.assigned.v1' : 'task.comment_mentioned.v1');
    event.data.recipient_id = recipient;
    assert.equal(await processEvent(pool, JSON.stringify(event)), 'created');
    const { rows } = await pool.query(
      `UPDATE notification_service.notifications SET created_at = $2
        WHERE event_id = $1 RETURNING id`,
      [event.id, createdAt[index]],
    );
    ids.push(rows[0].id);
  }
  return ids;
}

async function readAll(base, init, query) {
  const ids = [];
  let cursor = null;
  do {
    const url = `${base}?${query}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`;
    const page = await assertResponse(await fetch(url, init()), 200, 'NotificationPage');
    ids.push(...page.items.map((item) => item.id));
    cursor = page.next_cursor;
  } while (cursor);
  return ids;
}

test('pages follow created_at then id descending without gaps or other recipients', async (t) => {
  const { pool, base, as } = await setup(t);
  const recipient = randomUUID();
  const tie = '2026-11-14T10:00:00.500Z';
  const ids = await seed(pool, recipient, 7, [
    '2026-11-14T09:00:00.000Z', tie, tie, tie, '2026-11-14T11:00:00.000Z', tie, '2026-11-14T08:00:00.000Z',
  ]);
  await seed(pool, randomUUID(), 3, [tie, tie, tie]);

  const { rows } = await pool.query(
    `SELECT id FROM notification_service.notifications WHERE recipient_id = $1
      ORDER BY created_at DESC, id DESC`,
    [recipient],
  );
  const expected = rows.map((row) => row.id);
  assert.deepEqual([...expected].sort(), [...ids].sort());
  assert.deepEqual(await readAll(base, () => as(recipient), 'limit=2'), expected);
  assert.deepEqual(await readAll(base, () => as(recipient), 'limit=100'), expected);
});

test('unread filter, exact count and idempotent mark-as-read stay consistent', async (t) => {
  const { pool, base, as } = await setup(t);
  const recipient = randomUUID();
  const stranger = randomUUID();
  const ids = await seed(pool, recipient, 4, [
    '2026-11-14T10:00:00.001Z', '2026-11-14T10:00:00.002Z', '2026-11-14T10:00:00.003Z', '2026-11-14T10:00:00.004Z',
  ]);
  const count = async () => (await assertResponse(
    await fetch(`${base}/unread-count`, as(recipient)), 200, 'UnreadCount',
  )).count;
  assert.equal(await count(), 4);

  const results = await Promise.all(Array.from({ length: 8 }, async () => assertResponse(
    await fetch(`${base}/${ids[1]}/read`, { method: 'POST', ...as(recipient) }), 200, 'Notification',
  )));
  const readAt = results[0].read_at;
  assert.ok(readAt);
  assert.ok(results.every((item) => item.read_at === readAt));
  assert.equal(await count(), 3);

  const repeat = await assertResponse(
    await fetch(`${base}/${ids[1]}/read`, { method: 'POST', ...as(recipient) }), 200, 'Notification',
  );
  assert.equal(repeat.read_at, readAt);

  const denied = await fetch(`${base}/${ids[0]}/read`, { method: 'POST', ...as(stranger) });
  assert.equal((await assertResponse(denied, 404, 'Error')).error.code, 'NOT_FOUND');
  assert.equal(await count(), 3);

  const unread = await readAll(base, () => as(recipient), 'limit=1&unread_only=true');
  assert.deepEqual(unread, [ids[3], ids[2], ids[0]]);
  assert.equal((await readAll(base, () => as(recipient), 'limit=10')).length, 4);
  assert.deepEqual(await readAll(base, () => as(stranger), 'limit=10'), []);
});

test('a cursor from one recipient is refused for another', async (t) => {
  const { pool, base, as } = await setup(t);
  const recipient = randomUUID();
  await seed(pool, recipient, 2, ['2026-11-14T10:00:00.001Z', '2026-11-14T10:00:00.002Z']);
  const page = await (await fetch(`${base}?limit=1`, as(recipient))).json();
  const reused = await fetch(`${base}?limit=1&cursor=${encodeURIComponent(page.next_cursor)}`, as(randomUUID()));
  assert.equal((await assertResponse(reused, 400, 'Error')).error.code, 'INVALID_CURSOR');
});
