const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { test } = require('node:test');
const { brokerConfig, ConsumerMetrics, handleDelivery } = require('../../dist/consumer.js');
const { InvalidEventError } = require('../../dist/events.js');

const eventId = '00000000-0000-4000-8000-000000000102';
const requestId = '00000000-0000-4000-8000-000000000012';

function message(headers = {}) {
  return {
    content: Buffer.from(JSON.stringify({ id: eventId, request_id: requestId, data: { secret: 'private' } })),
    fields: { deliveryTag: 1 },
    properties: { messageId: eventId, type: 'task.assigned.v1', contentType: 'application/json', headers },
  };
}

function channel({ confirm = true, routed = true } = {}) {
  const fake = new EventEmitter();
  fake.published = [];
  fake.acked = [];
  fake.publish = (exchange, routingKey, content, options, callback) => {
    fake.published.push({ exchange, routingKey, content, options });
    setImmediate(() => {
      if (!routed) fake.emit('return', {});
      if (confirm === 'never') return;
      callback(confirm ? null : new Error('nacked'));
    });
    return true;
  };
  fake.ack = (delivered) => fake.acked.push(delivered);
  return fake;
}

async function deliver(fakeChannel, delivered, result, options = {}) {
  const metrics = new ConsumerMetrics();
  const logs = [];
  const run = handleDelivery(fakeChannel, delivered, async () => {
    if (result instanceof Error) throw result;
    return result;
  }, metrics, { confirmTimeoutMs: 50, log: (event, fields) => logs.push({ event, ...fields }), ...options });
  return { run, metrics, logs };
}

test('processed events are acknowledged and counted by outcome', async () => {
  for (const outcome of ['created', 'suppressed', 'duplicate']) {
    const fake = channel();
    const delivered = message();
    const { run, metrics, logs } = await deliver(fake, delivered, outcome);
    await run;
    assert.deepEqual(fake.acked, [delivered]);
    assert.equal(fake.published.length, 0);
    assert.equal(metrics.processed[outcome], 1);
    assert.deepEqual(logs, [{
      event: 'event_processed', event_id: eventId, request_id: requestId, type: 'task.assigned.v1',
      attempt: 1, outcome,
    }]);
  }
});

test('transient failures follow the 5s, 30s, 2m, 10m, 30m retry schedule', async () => {
  const schedule = ['5s', '30s', '2m', '10m', '30m'];
  for (const [index, delay] of schedule.entries()) {
    const attempt = index + 1;
    const fake = channel();
    const delivered = message(attempt === 1 ? {} : { 'x-attempt': attempt, 'x-trace': 'kept' });
    const { run, metrics } = await deliver(fake, delivered, new Error('database down'));
    await run;
    const [forwarded] = fake.published;
    assert.equal(forwarded.exchange, 'retry');
    assert.equal(forwarded.routingKey, `notifications.v1.retry.${delay}`);
    assert.equal(forwarded.options.headers['x-attempt'], attempt + 1);
    if (attempt > 1) assert.equal(forwarded.options.headers['x-trace'], 'kept');
    assert.equal(forwarded.options.messageId, eventId);
    assert.equal(forwarded.options.persistent, true);
    assert.equal(forwarded.options.mandatory, true);
    assert.equal(forwarded.content, delivered.content);
    assert.deepEqual(fake.acked, [delivered]);
    assert.equal(metrics.retries, 1);
    assert.equal(metrics.failures, 1);
  }
});

test('the sixth failure, invalid events and bad attempt headers go to the dead-letter queue', async () => {
  for (const [headers, result, reason] of [
    [{ 'x-attempt': 6 }, new Error('still down'), 'retries_exhausted'],
    [{}, new InvalidEventError('bad'), 'invalid_event'],
    [{ 'x-attempt': 7 }, 'created', 'invalid_attempt_header'],
    [{ 'x-attempt': 'two' }, 'created', 'invalid_attempt_header'],
  ]) {
    const fake = channel();
    const delivered = message(headers);
    const { run, metrics, logs } = await deliver(fake, delivered, result);
    await run;
    assert.equal(fake.published.length, 1, reason);
    assert.equal(fake.published[0].exchange, 'dead-letter');
    assert.equal(fake.published[0].routingKey, 'notifications.v1');
    assert.equal(fake.published[0].options.messageId, eventId);
    assert.deepEqual(fake.acked, [delivered]);
    assert.equal(metrics.deadLetters, 1);
    assert.equal(logs[0].reason, reason);
    assert.ok(!JSON.stringify(logs).includes('private'));
  }
});

test('an unconfirmed, nacked or unroutable forward leaves the original unacknowledged', async () => {
  for (const options of [{ confirm: false }, { confirm: 'never' }, { routed: false }]) {
    const fake = channel(options);
    const { run, metrics } = await deliver(fake, message(), new Error('database down'));
    await assert.rejects(run);
    assert.deepEqual(fake.acked, []);
    assert.equal(metrics.retries, 0);
    assert.equal(fake.listenerCount('return'), 0);
  }
});

test('broker settings come from the environment and the password is required', () => {
  assert.equal(brokerConfig({ BROKER_HOST: 'broker' }), undefined);
  assert.deepEqual(brokerConfig({ BROKER_HOST: 'broker', BROKER_PASSWORD: 'secret' }), {
    host: 'broker', port: 5672, vhost: 'taskmgn', username: 'notification', password: 'secret', tls: false,
  });
});

test('consumer metrics use fixed names without event content', () => {
  const metrics = new ConsumerMetrics();
  metrics.processed.created = 2;
  metrics.deadLetters = 1;
  const text = metrics.lines().join('\n');
  assert.match(text, /notification_consumer_events_total\{outcome="created"\} 2/);
  assert.match(text, /notification_consumer_dead_letters_total 1/);
  assert.match(text, /notification_consumer_retries_total 0/);
});
