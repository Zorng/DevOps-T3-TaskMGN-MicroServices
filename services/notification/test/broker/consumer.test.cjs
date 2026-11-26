const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { test, beforeEach, afterEach, after } = require('node:test');
const { setTimeout: sleep } = require('node:timers/promises');
const amqp = require('amqplib');
const { ConsumerMetrics, queue, startConsumer } = require('../../dist/consumer.js');
const { createPool } = require('../../dist/dependencies.js');
const { processEvent } = require('../../dist/processing.js');
const { freshEvent } = require('../events.cjs');

for (const name of ['DATABASE_URL', 'BROKER_HOST', 'BROKER_PASSWORD', 'BROKER_ADMIN_USER', 'BROKER_ADMIN_PASSWORD']) {
  assert.ok(process.env[name], `Set ${name}; see the README section on broker tests`);
}

const env = process.env;
const vhost = env.BROKER_VHOST ?? 'taskmgn';
const port = Number(env.BROKER_PORT ?? '5672');
const managementUrl = env.BROKER_MGMT_URL ?? 'http://127.0.0.1:15672';
const config = {
  host: env.BROKER_HOST, port, vhost, username: env.BROKER_USERNAME ?? 'notification',
  password: env.BROKER_PASSWORD, tls: false,
};
const queues = [queue, `${queue}.dlq`, ...['5s', '30s', '2m', '10m', '30m'].map((delay) => `${queue}.retry.${delay}`)];
const pool = createPool(env.DATABASE_URL);
const real = (content) => processEvent(pool, content);
const never = () => new Promise(() => {});

let admin;
let channel;
const running = new Set();

async function adminChannel() {
  if (!channel) {
    admin = await amqp.connect({
      hostname: env.BROKER_HOST, port, vhost, username: env.BROKER_ADMIN_USER, password: env.BROKER_ADMIN_PASSWORD,
    });
    channel = await admin.createConfirmChannel();
  }
  return channel;
}

async function publish(exchange, routingKey, content, properties = {}) {
  const ch = await adminChannel();
  await new Promise((resolve, reject) => ch.publish(exchange, routingKey, Buffer.from(content), {
    persistent: true, contentType: 'application/json', ...properties,
  }, (error) => (error ? reject(error) : resolve())));
}

const publishEvent = (event, headers) => publish('events', event.type, JSON.stringify(event), {
  messageId: event.id, type: event.type, headers,
});

function consumer(processMessage = real) {
  const metrics = new ConsumerMetrics();
  const logs = [];
  const handle = startConsumer(config, processMessage, metrics, {
    reconnectDelayMs: 200, log: (event, fields) => logs.push({ event, ...fields }),
  });
  running.add(handle);
  const stop = async () => {
    running.delete(handle);
    await handle.stop();
  };
  return { metrics, logs, stop };
}

async function until(check, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (!(await check())) {
    if (Date.now() > deadline) throw new Error('Timed out waiting for the consumer');
    await sleep(50);
  }
}

async function depth(name) {
  return (await (await adminChannel()).checkQueue(name)).messageCount;
}

async function rows(eventId) {
  const result = await pool.query('SELECT id FROM notification_service.notifications WHERE event_id = $1', [eventId]);
  return result.rowCount;
}

async function takeDeadLetter() {
  const ch = await adminChannel();
  await until(async () => (await depth(`${queue}.dlq`)) > 0);
  const message = await ch.get(`${queue}.dlq`, { noAck: false });
  ch.ack(message);
  return message;
}

beforeEach(async () => {
  const ch = await adminChannel();
  for (const name of queues) await ch.purgeQueue(name);
});

afterEach(async () => {
  for (const handle of running) await handle.stop();
  running.clear();
});

after(async () => {
  await admin?.close();
  await pool.end();
});

test('notification events are consumed and acknowledged only after commit', async () => {
  const { metrics, logs, stop } = consumer();
  const events = ['invitation.created.v1', 'task.assigned.v1', 'task.comment_mentioned.v1'].map((type) => freshEvent(type));
  for (const event of events) await publishEvent(event);
  await until(() => metrics.processed.created === 3);
  await stop();

  for (const event of events) assert.equal(await rows(event.id), 1);
  assert.equal(await depth(queue), 0);
  const processed = logs.filter((log) => log.event === 'event_processed');
  assert.deepEqual(processed.map((log) => log.event_id).sort(), events.map((event) => event.id).sort());
  assert.ok(processed.every((log) => log.request_id === events[0].request_id && log.attempt === 1));
});

test('a redelivered event is a duplicate and creates no second notification', async () => {
  const { metrics, stop } = consumer();
  const event = freshEvent('task.assigned.v1');
  await publishEvent(event);
  await publishEvent(event);
  await until(() => metrics.processed.created + metrics.processed.duplicate === 2);
  await stop();
  assert.deepEqual(metrics.processed, { created: 1, suppressed: 0, duplicate: 1 });
  assert.equal(await rows(event.id), 1);
});

test('a crash after commit but before acknowledgement is redelivered as a duplicate', async () => {
  let committed = false;
  const crashing = consumer(async (content) => {
    await real(content);
    committed = true;
    return never();
  });
  const event = freshEvent('task.comment_mentioned.v1');
  await publishEvent(event);
  await until(() => committed);
  await crashing.stop();

  const { metrics, stop } = consumer();
  await until(() => metrics.processed.duplicate === 1);
  await stop();
  assert.equal(await rows(event.id), 1);
  assert.equal(await depth(queue), 0);
});

test('a crash before commit is redelivered and processed once', async () => {
  let started = false;
  const crashing = consumer(async () => {
    started = true;
    return never();
  });
  const event = freshEvent('invitation.created.v1');
  await publishEvent(event);
  await until(() => started);
  assert.equal(await rows(event.id), 0);
  await crashing.stop();

  const { metrics, stop } = consumer();
  await until(() => metrics.processed.created === 1);
  await stop();
  assert.equal(await rows(event.id), 1);
});

test('a database outage is retried through the delay queue with the next attempt number', async () => {
  const brokenPool = createPool('postgresql://nobody:none@127.0.0.1:1/missing');
  const event = freshEvent('task.assigned.v1');
  let failed = false;
  const { metrics, logs, stop } = consumer((content) => {
    if (failed || !content.includes(event.id)) return real(content);
    failed = true;
    return processEvent(brokenPool, content);
  });
  await publishEvent(event);
  await until(() => metrics.retries === 1);
  assert.equal(await depth(`${queue}.retry.5s`), 1);
  await until(() => metrics.processed.created === 1, 15000);
  await stop();
  await brokenPool.end();

  assert.equal(await rows(event.id), 1);
  assert.deepEqual(
    logs.filter((log) => log.event_id === event.id).map((log) => [log.event, log.attempt]),
    [['event_retry_scheduled', 1], ['event_processed', 2]],
  );
});

test('poison messages are dead-lettered at once without retry', async () => {
  const { metrics, stop } = consumer();
  const unsupported = { ...freshEvent('task.assigned.v1'), type: 'workspace.deleted.v1' };
  await publish('retry-return', queue, '{not json', { messageId: randomUUID() });
  await publish('retry-return', queue, JSON.stringify(unsupported), { messageId: unsupported.id });
  await until(() => metrics.deadLetters === 2);
  await stop();

  assert.equal(metrics.invalid, 2);
  assert.equal(metrics.retries, 0);
  assert.equal(await depth(`${queue}.dlq`), 2);
  assert.equal(await rows(unsupported.id), 0);
});

test('the sixth failure is dead-lettered and a manual replay creates exactly one notification', async () => {
  const failing = consumer(async () => { throw new Error('still failing'); });
  const event = freshEvent('task.comment_mentioned.v1');
  await publishEvent(event, { 'x-attempt': 6 });
  await until(() => failing.metrics.deadLetters === 1);
  await failing.stop();
  assert.equal(failing.metrics.retries, 0);

  const dead = await takeDeadLetter();
  assert.equal(dead.properties.messageId, event.id);
  assert.equal(dead.properties.headers['x-attempt'], 6);
  assert.equal(await rows(event.id), 0);

  const { 'x-death': _death, 'x-first-death-exchange': _first, 'x-last-death-exchange': _last, ...headers } =
    dead.properties.headers;
  const replay = () => publish('retry-return', queue, dead.content, {
    messageId: dead.properties.messageId, type: dead.properties.type, headers,
  });
  const { metrics, stop } = consumer();
  await replay();
  await until(() => metrics.processed.created === 1);
  await replay();
  await until(() => metrics.processed.duplicate === 1);
  await stop();
  assert.equal(await rows(event.id), 1);
});

test('the consumer reconnects after the broker closes its connection', async () => {
  const auth = 'Basic ' + Buffer.from(`${env.BROKER_ADMIN_USER}:${env.BROKER_ADMIN_PASSWORD}`).toString('base64');
  const { metrics, stop } = consumer();
  await until(async () => (await connections()).length === 1);
  for (const name of await connections()) {
    await fetch(`${managementUrl}/api/connections/${encodeURIComponent(name)}`, {
      method: 'DELETE', headers: { Authorization: auth },
    });
  }
  await until(() => metrics.disconnects >= 1);

  const event = freshEvent('task.assigned.v1');
  await publishEvent(event);
  await until(() => metrics.processed.created === 1);
  await stop();
  assert.equal(await rows(event.id), 1);

  async function connections() {
    const response = await fetch(`${managementUrl}/api/connections?columns=name,user`, { headers: { Authorization: auth } });
    return (await response.json()).filter((item) => item.user === config.username).map((item) => item.name);
  }
});

test('competing consumers share the queue and process each event once', async () => {
  const first = consumer();
  const second = consumer();
  await sleep(300);
  const events = Array.from({ length: 30 }, () => freshEvent('task.assigned.v1'));
  for (const event of events) await publishEvent(event);
  await until(() => first.metrics.processed.created + second.metrics.processed.created === 30);
  await first.stop();
  await second.stop();

  assert.ok(first.metrics.processed.created > 0 && second.metrics.processed.created > 0);
  for (const event of events) assert.equal(await rows(event.id), 1);
});
