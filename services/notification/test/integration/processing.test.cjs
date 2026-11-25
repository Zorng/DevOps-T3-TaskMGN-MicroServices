const assert = require('node:assert/strict');
const { test } = require('node:test');
const { createPool } = require('../../dist/dependencies.js');
const { InvalidEventError } = require('../../dist/events.js');
const { consumerName, processEvent } = require('../../dist/processing.js');
const { freshEvent } = require('../events.cjs');

function database(t) {
  assert.ok(process.env.DATABASE_URL, 'Set DATABASE_URL to a migrated disposable test database');
  const pool = createPool(process.env.DATABASE_URL);
  t.after(() => pool.end());
  return pool;
}

const send = (pool, event) => processEvent(pool, JSON.stringify(event));

async function stored(pool, eventId) {
  const notifications = await pool.query(
    'SELECT * FROM notification_service.notifications WHERE event_id = $1', [eventId],
  );
  const markers = await pool.query(
    'SELECT 1 FROM notification_service.processed_events WHERE consumer_name = $1 AND event_id = $2',
    [consumerName, eventId],
  );
  return { notifications: notifications.rows, markers: markers.rowCount };
}

test('each notification event type stores one generic unread notification', async (t) => {
  const pool = database(t);
  const expected = {
    'invitation.created.v1': { type: 'invitation', invitation: true, task: false, comment: false },
    'task.assigned.v1': { type: 'assignment', invitation: false, task: true, comment: false },
    'task.comment_mentioned.v1': { type: 'mention', invitation: false, task: true, comment: true },
  };
  for (const [eventType, shape] of Object.entries(expected)) {
    const event = freshEvent(eventType);
    assert.equal(await send(pool, event), 'created');
    const { notifications, markers } = await stored(pool, event.id);
    assert.equal(markers, 1);
    assert.equal(notifications.length, 1);
    const [row] = notifications;
    assert.deepEqual(Object.keys(row).sort(), [
      'actor_display_name', 'actor_id', 'comment_id', 'created_at', 'event_id', 'id', 'invitation_id',
      'occurred_at', 'read_at', 'recipient_id', 'task_id', 'type', 'workspace_id',
    ]);
    assert.equal(row.type, shape.type);
    assert.equal(row.recipient_id, event.data.recipient_id);
    assert.equal(row.actor_id, event.data.actor.id);
    assert.equal(row.actor_display_name, event.data.actor.display_name);
    assert.equal(row.workspace_id, event.data.workspace_id);
    assert.equal(row.invitation_id, shape.invitation ? event.data.invitation_id : null);
    assert.equal(row.task_id, shape.task ? event.data.task_id : null);
    assert.equal(row.comment_id, shape.comment ? event.data.comment_id : null);
    assert.equal(row.occurred_at.toISOString(), new Date(event.occurred_at).toISOString());
    assert.ok(row.created_at instanceof Date);
    assert.equal(row.read_at, null);
  }
});

test('a replay after commit is a harmless duplicate', async (t) => {
  const pool = database(t);
  const event = freshEvent('task.assigned.v1');
  assert.equal(await send(pool, event), 'created');
  assert.equal(await send(pool, event), 'duplicate');
  assert.equal((await stored(pool, event.id)).notifications.length, 1);
});

test('concurrent deliveries of one event create exactly one notification', async (t) => {
  const pool = database(t);
  const event = freshEvent('task.comment_mentioned.v1');
  const results = await Promise.all(Array.from({ length: 6 }, () => send(pool, event)));
  assert.deepEqual(results.sort(), ['created', 'duplicate', 'duplicate', 'duplicate', 'duplicate', 'duplicate']);
  const { notifications, markers } = await stored(pool, event.id);
  assert.equal(notifications.length, 1);
  assert.equal(markers, 1);
});

test('self-notifications are suppressed but still marked processed', async (t) => {
  const pool = database(t);
  const event = freshEvent('task.comment_mentioned.v1');
  event.data.recipient_id = event.data.actor.id;
  assert.equal(await send(pool, event), 'suppressed');
  assert.equal(await send(pool, event), 'duplicate');
  assert.deepEqual(await stored(pool, event.id), { notifications: [], markers: 1 });
});

test('a failure before commit rolls back the marker so the event can be retried', async (t) => {
  const pool = database(t);
  const event = freshEvent('invitation.created.v1');
  const failing = {
    connect: async () => {
      const client = await pool.connect();
      const query = client.query.bind(client);
      client.query = (text, ...rest) => {
        if (typeof text === 'string' && text.includes('INTO notification_service.notifications')) {
          return Promise.reject(new Error('simulated crash'));
        }
        return query(text, ...rest);
      };
      return client;
    },
  };
  await assert.rejects(processEvent(failing, JSON.stringify(event)), /simulated crash/);
  assert.deepEqual(await stored(pool, event.id), { notifications: [], markers: 0 });

  assert.equal(await send(pool, event), 'created');
  assert.equal((await stored(pool, event.id)).notifications.length, 1);
});

test('invalid events are rejected before any database write', async (t) => {
  const pool = database(t);
  const event = freshEvent('task.assigned.v1', { producer: 'identity-workspace' });
  await assert.rejects(send(pool, event), InvalidEventError);
  assert.deepEqual(await stored(pool, event.id), { notifications: [], markers: 0 });
});
