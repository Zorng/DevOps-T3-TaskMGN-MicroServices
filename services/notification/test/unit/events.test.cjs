const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { InvalidEventError, parseEvent } = require('../../dist/events.js');
const { contracts, example } = require('../events.cjs');

const parse = (event) => parseEvent(JSON.stringify(event));
const rejects = (event, message) => assert.throws(() => parse(event), (error) => {
  assert.ok(error instanceof InvalidEventError);
  assert.match(error.message, message);
  return true;
});

test('the bundled schema matches the shared event contract', () => {
  const bundled = readFileSync(resolve(__dirname, '../../src/contracts/event.schema.json'), 'utf8');
  const shared = readFileSync(resolve(contracts, 'events/event.schema.json'), 'utf8');
  assert.deepEqual(JSON.parse(bundled), JSON.parse(shared));
});

test('contract examples map to generic notifications with only contracted metadata', () => {
  const common = {
    recipientId: '00000000-0000-4000-8000-000000000002',
    actorId: '00000000-0000-4000-8000-000000000001',
    actorDisplayName: 'Example Owner',
    workspaceId: '00000000-0000-4000-8000-000000000003',
    occurredAt: '2026-11-14T10:00:00Z',
  };
  assert.deepEqual(parse(example('invitation.created.v1')), {
    ...common, eventId: '00000000-0000-4000-8000-000000000101', type: 'invitation',
    invitationId: '00000000-0000-4000-8000-000000000008', taskId: null, commentId: null,
  });
  assert.deepEqual(parse(example('task.assigned.v1')), {
    ...common, eventId: '00000000-0000-4000-8000-000000000102', type: 'assignment',
    invitationId: null, taskId: '00000000-0000-4000-8000-000000000006', commentId: null,
  });
  assert.deepEqual(parse(example('task.comment_mentioned.v1')), {
    ...common, eventId: '00000000-0000-4000-8000-000000000103', type: 'mention',
    invitationId: null, taskId: '00000000-0000-4000-8000-000000000006',
    commentId: '00000000-0000-4000-8000-000000000007',
  });
  assert.equal(parseEvent(Buffer.from(JSON.stringify(example('task.assigned.v1')))).type, 'assignment');
});

test('workspace cleanup events are valid but not handled by Notification', () => {
  rejects(example('workspace.member_removed.v1'), /not handled/);
  rejects(example('workspace.deleted.v1'), /not handled/);
});

test('malformed and oversized messages are rejected', () => {
  assert.throws(() => parseEvent('{not json'), /not valid JSON/);
  assert.throws(() => parseEvent('x'.repeat(64 * 1024 + 1)), /exceeds 64 KiB/);
  for (const value of [null, [], 'text', 42]) rejects(value, /contract schema/);
});

test('events that break the schema contract are rejected', () => {
  const cases = {
    'wrong producer': (e) => { e.producer = 'identity-workspace'; },
    'unknown type': (e) => { e.type = 'task.updated.v1'; },
    'future schema version': (e) => { e.schema_version = 2; },
    'missing envelope field': (e) => { delete e.request_id; },
    'extra envelope field': (e) => { e.title = 'Private task title'; },
    'extra data field': (e) => { e.data.recipient_email = 'person@example.com'; },
    'missing recipient': (e) => { delete e.data.recipient_id; },
    'invalid event ID': (e) => { e.id = 'not-a-uuid'; },
    'invalid timestamp': (e) => { e.occurred_at = 'yesterday'; },
    'fractional version': (e) => { e.aggregate_version = 1.5; },
    'empty display name': (e) => { e.data.actor.display_name = ''; },
    'long display name': (e) => { e.data.actor.display_name = 'x'.repeat(201); },
  };
  for (const [name, change] of Object.entries(cases)) {
    const event = example('task.assigned.v1');
    change(event);
    assert.throws(() => parse(event), InvalidEventError, name);
  }
});

test('an aggregate that disagrees with the event data is rejected', () => {
  for (const type of ['invitation.created.v1', 'task.assigned.v1', 'task.comment_mentioned.v1']) {
    const event = example(type);
    event.aggregate_id = '00000000-0000-4000-8000-000000000999';
    rejects(event, /aggregate/);
  }
});
