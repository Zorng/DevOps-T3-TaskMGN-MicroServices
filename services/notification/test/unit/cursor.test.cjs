const assert = require('node:assert/strict');
const { test } = require('node:test');
const { createCursor, InvalidCursorError } = require('../../dist/cursor.js');

const secret = 'unit-test-cursor-secret-with-32-bytes';
const recipient = '6f1c3a52-0e0b-4c1e-9d55-6a2b2c5f6a01';
const other = '0c7d3f4e-5a6b-4c8d-9e0f-1a2b3c4d5e6f';
const position = { createdAt: '2026-11-14T10:00:00.123Z', id: '9b2f7a10-3c4d-4e5f-8a6b-7c8d9e0f1a2b' };

test('a cursor round-trips only for the same recipient and filter', () => {
  const cursor = createCursor(secret);
  const token = cursor.encode(recipient, true, position);
  assert.ok(token.length <= 1024);
  assert.deepEqual(cursor.decode(token, recipient, true), position);
  assert.throws(() => cursor.decode(token, other, true), InvalidCursorError);
  assert.throws(() => cursor.decode(token, recipient, false), InvalidCursorError);
});

test('forged, tampered and malformed cursors are rejected', () => {
  const cursor = createCursor(secret);
  const token = cursor.encode(recipient, false, position);
  const [data, signature] = token.split('.');
  const forgedData = Buffer.from(`1|${recipient}|all|2099-01-01T00:00:00.000Z|${position.id}`).toString('base64url');
  const invalid = [
    createCursor('another-secret-that-is-also-32-bytes-long').encode(recipient, false, position),
    `${forgedData}.${signature}`,
    `${data}.${signature.slice(0, -2)}`,
    `${data}.${signature}.extra`,
    data,
    'not-a-cursor',
  ];
  for (const value of invalid) {
    assert.throws(() => cursor.decode(value, recipient, false), InvalidCursorError, value);
  }
});
