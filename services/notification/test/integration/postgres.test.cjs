const assert = require('node:assert/strict');
const { test } = require('node:test');
const { randomUUID } = require('node:crypto');
const { createApp } = require('../../dist/app.js');
const { checkDependencies, createPool } = require('../../dist/dependencies.js');
const { listen } = require('../http.cjs');

test('readiness uses migrated PostgreSQL and a controlled Identity health server', async (t) => {
  assert.ok(process.env.DATABASE_URL, 'Set DATABASE_URL to a migrated disposable test database');
  const pool = createPool(process.env.DATABASE_URL);
  t.after(() => pool.end());
  let identityStatus = 200;
  const identity = await listen(t, (request, response) => {
    assert.equal(request.url, '/health/ready');
    assert.ok(request.headers['x-request-id']);
    response.writeHead(identityStatus).end();
  });
  const base = await listen(t, createApp((id) => checkDependencies(pool, identity + '/health/ready', id)));

  for (const [status, expected] of [[200, 200], [503, 503], [200, 200]]) {
    identityStatus = status;
    const response = await fetch(base + '/health/ready');
    assert.equal(response.status, expected);
    assert.deepEqual(await response.json(), { status: expected === 200 ? 'ok' : 'unavailable' });
  }

  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const event = randomUUID();
    const recipient = randomUUID();
    const insert = (recipientId) => client.query(
      `INSERT INTO notification_service.notifications
        (id, event_id, recipient_id, type, actor_id, actor_display_name, workspace_id, occurred_at)
        VALUES ($1, $2, $3, 'invitation', $4, 'Test Actor', $5, now())
        RETURNING read_at`,
      [randomUUID(), event, recipientId, randomUUID(), randomUUID()],
    );
    assert.equal((await insert(recipient)).rows[0].read_at, null);
    await insert(randomUUID());
    await assert.rejects(insert(recipient), { code: '23505' });
  } finally {
    try {
      await client.query('ROLLBACK');
    } finally {
      client.release();
    }
  }
});
