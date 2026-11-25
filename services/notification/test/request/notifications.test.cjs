const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { test } = require('node:test');
const { createApp } = require('../../dist/app.js');
const { createNotificationRoutes } = require('../../dist/routes.js');
const { assertResponse } = require('../contract.cjs');
const { listen } = require('../http.cjs');

const secret = 'request-test-cursor-secret-32-bytes!';
const recipient = randomUUID();
const other = randomUUID();
const owned = randomUUID();

function row(id, readAt = null) {
  return {
    id,
    event_id: randomUUID(),
    type: 'mention',
    actor_id: randomUUID(),
    actor_display_name: 'Example Owner',
    workspace_id: randomUUID(),
    invitation_id: null,
    task_id: randomUUID(),
    comment_id: randomUUID(),
    occurred_at: new Date('2026-11-14T10:00:00Z'),
    created_at: new Date('2026-11-14T10:00:00.500Z'),
    read_at: readAt,
  };
}

async function setup(t, { session = { kind: 'verified', recipientId: recipient }, pool, cursorSecret = secret } = {}) {
  const calls = [];
  const database = pool !== undefined ? pool : {
    query: async (sql, params) => {
      calls.push({ sql, params });
      if (sql.includes('count(*)')) return { rows: [{ count: 2 }] };
      if (sql.includes('UPDATE')) {
        return { rows: params[0] === owned && params[1] === recipient ? [row(owned, new Date())] : [] };
      }
      return { rows: [row(randomUUID()), row(randomUUID()), row(randomUUID())].slice(0, params[4]) };
    },
  };
  const app = createApp(async () => {}, {
    introspect: async () => session,
    routes: createNotificationRoutes(database, cursorSecret),
  });
  const base = (await listen(t, app)) + '/api/v1/notifications';
  return { base, calls };
}

const auth = { Authorization: 'Bearer controlled-session', 'X-User-ID': other };

test('listing returns a contract page for the verified recipient only', async (t) => {
  const { base, calls } = await setup(t);
  const requestId = randomUUID();
  const page = await assertResponse(
    await fetch(`${base}?limit=2&unread_only=true`, {
      headers: { ...auth, 'X-Request-ID': requestId },
    }),
    200, 'NotificationPage', requestId,
  );
  assert.equal(page.items.length, 2);
  assert.ok(page.next_cursor);
  assert.deepEqual(calls[0].params.slice(0, 2), [recipient, true]);
  assert.equal(calls[0].params[4], 3);

  const next = await assertResponse(
    await fetch(`${base}?limit=5&unread_only=true&cursor=${encodeURIComponent(page.next_cursor)}`, { headers: auth }),
    200, 'NotificationPage',
  );
  assert.equal(next.next_cursor, null);
  assert.equal(calls[1].params[0], recipient);
  assert.ok(calls[1].params[2]);

  await assertResponse(await fetch(base, { headers: auth }), 200, 'NotificationPage');
  assert.deepEqual(calls[2].params.slice(0, 2), [recipient, false]);
  assert.equal(calls[2].params[4], 21);
});

test('listing rejects invalid parameters and cursors from another scope', async (t) => {
  const { base, calls } = await setup(t);
  for (const query of [
    'limit=0', 'limit=101', 'limit=abc', 'limit=', 'limit=1&limit=2', 'unread_only=yes', 'unread_only=1',
    'cursor=', `cursor=${'x'.repeat(1025)}`, `recipient_id=${other}`, 'sort=asc',
  ]) {
    const body = await assertResponse(await fetch(`${base}?${query}`, { headers: auth }), 400, 'Error');
    assert.equal(body.error.code, 'MALFORMED_REQUEST', query);
  }

  const page = await (await fetch(`${base}?limit=1`, { headers: auth })).json();
  const { base: otherBase } = await setup(t, { session: { kind: 'verified', recipientId: other } });
  for (const url of [
    `${otherBase}?limit=1&cursor=${encodeURIComponent(page.next_cursor)}`,
    `${base}?limit=1&unread_only=true&cursor=${encodeURIComponent(page.next_cursor)}`,
    `${base}?cursor=forged.cursor`,
  ]) {
    const body = await assertResponse(await fetch(url, { headers: auth }), 400, 'Error');
    assert.equal(body.error.code, 'INVALID_CURSOR');
  }
  assert.equal(calls.length, 1);
});

test('unread count is exact for the verified recipient and accepts no input', async (t) => {
  const { base, calls } = await setup(t);
  const body = await assertResponse(await fetch(`${base}/unread-count`, { headers: auth }), 200, 'UnreadCount');
  assert.deepEqual(body, { count: 2 });
  assert.deepEqual(calls[0].params, [recipient]);

  for (const url of [`${base}/unread-count?recipient_id=${other}`, `${base}/unread-count?unread_only=true`]) {
    const error = await assertResponse(await fetch(url, { headers: auth }), 400, 'Error');
    assert.equal(error.error.code, 'MALFORMED_REQUEST');
  }
  assert.equal(calls.length, 1);
});

test('mark-as-read updates only the recipient notification and rejects input', async (t) => {
  const { base, calls } = await setup(t);
  const read = await assertResponse(
    await fetch(`${base}/${owned}/read`, { method: 'POST', headers: auth }), 200, 'Notification',
  );
  assert.equal(read.id, owned);
  assert.ok(read.read_at);
  assert.deepEqual(calls[0].params, [owned, recipient]);

  const missing = await assertResponse(
    await fetch(`${base}/${randomUUID()}/read`, { method: 'POST', headers: auth }), 404, 'Error',
  );
  assert.equal(missing.error.code, 'NOT_FOUND');

  for (const [url, init, status, code] of [
    [`${base}/not-a-uuid/read`, {}, 400, 'MALFORMED_REQUEST'],
    [`${base}/${owned}/read`, { body: '{}', headers: { 'Content-Type': 'application/json' } }, 400, 'MALFORMED_REQUEST'],
    [`${base}/${owned}/read?force=true`, {}, 400, 'MALFORMED_REQUEST'],
    [`${base}/${owned}/read`, { body: 'x', headers: { 'Content-Type': 'text/plain' } }, 415, 'UNSUPPORTED_MEDIA_TYPE'],
  ]) {
    const response = await fetch(url, { method: 'POST', ...init, headers: { ...auth, ...init.headers } });
    const body = await assertResponse(response, status, 'Error');
    assert.equal(body.error.code, code, url);
  }
  assert.equal(calls.length, 2);
});

test('every endpoint is guarded before any storage access', async (t) => {
  const endpoints = [
    ['GET', ''],
    ['GET', '/unread-count'],
    ['POST', `/${owned}/read`],
  ];
  for (const [session, headers, status, code] of [
    [undefined, { 'X-User-ID': recipient }, 401, 'AUTH_REQUIRED'],
    [{ kind: 'invalid' }, auth, 401, 'SESSION_INVALID'],
    [{ kind: 'unavailable' }, auth, 503, 'DEPENDENCY_UNAVAILABLE'],
  ]) {
    const { base, calls } = await setup(t, { session });
    for (const [method, path] of endpoints) {
      const response = await fetch(base + path, { method, headers });
      assert.equal((await assertResponse(response, status, 'Error')).error.code, code);
    }
    assert.equal(calls.length, 0);
  }
});

test('missing storage or cursor secret fails closed with Retry-After', async (t) => {
  const { base: noDatabase } = await setup(t, { pool: null });
  for (const [method, path] of [['GET', ''], ['GET', '/unread-count'], ['POST', `/${owned}/read`]]) {
    const response = await fetch(noDatabase + path, { method, headers: auth });
    assert.equal(response.headers.get('retry-after'), '1');
    assert.equal((await assertResponse(response, 503, 'Error')).error.code, 'DEPENDENCY_UNAVAILABLE');
  }
  const { base: shortSecret } = await setup(t, { cursorSecret: 'too-short' });
  const response = await fetch(shortSecret, { headers: auth });
  assert.equal((await assertResponse(response, 503, 'Error')).error.code, 'DEPENDENCY_UNAVAILABLE');
});
