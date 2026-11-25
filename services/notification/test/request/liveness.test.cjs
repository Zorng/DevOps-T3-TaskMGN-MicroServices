const assert = require('node:assert/strict');
const { once } = require('node:events');
const { randomUUID } = require('node:crypto');
const { after, before, test } = require('node:test');
const { createApp } = require('../../dist/app.js');

let server;
let url;

before(async () => {
  server = createApp(async () => {}).listen(0, '127.0.0.1');
  await once(server, 'listening');
  url = `http://127.0.0.1:${server.address().port}/health/live`;
});

after(async () => {
  await new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
});

test('liveness returns the contract response and preserves a valid request ID', async () => {
  const requestId = randomUUID();
  const response = await fetch(url, { headers: { 'X-Request-ID': requestId } });

  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('x-request-id'), requestId);
  assert.deepEqual(await response.json(), { status: 'ok' });
});

test('liveness supplies a UUID for missing and invalid request IDs', async () => {
  for (const headers of [{}, { 'X-Request-ID': 'invalid' }]) {
    const response = await fetch(url, { headers });
    assert.match(response.headers.get('x-request-id'), /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i);
    await response.json();
  }
});
