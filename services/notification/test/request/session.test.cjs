const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { test } = require('node:test');
const { Router } = require('express');
const { createApp } = require('../../dist/app.js');
const { createIdentityClient } = require('../../dist/identity.js');
const { listen } = require('../http.cjs');

test('the notification route guard scopes reads and writes to a freshly verified recipient', async (t) => {
  const recipient = randomUUID();
  const forged = randomUUID();
  const requestId = randomUUID();
  const headers = {
    Authorization: 'Bearer controlled-session',
    'X-Request-ID': requestId,
    'X-User-ID': forged,
    'X-User-Role': 'owner',
    'X-Service-Key': 'forged-key',
  };
  const trustedKey = 'controlled-notification-key';
  const calls = [];
  let status = 200;
  let code = 'SESSION_INVALID';
  const identity = await listen(t, (request, response) => {
    calls.push({ method: request.method, url: request.url, headers: request.headers });
    response.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(
      status === 200
        ? { user: { id: recipient, display_name: 'Verified User' }, session_expires_at: '2099-01-01T00:00:00Z' }
        : { error: { code, message: 'private diagnostic' } },
    ));
  });

  // Test-only handlers stand in for #42's reads and writes.
  const data = new Map([[recipient, 'unread'], [forged, 'other recipient']]);
  const read = t.mock.fn((id) => data.get(id));
  const write = t.mock.fn((id) => data.set(id, 'read'));
  const routes = Router();
  routes.get('/', (_request, response) => response.json(read(response.locals.recipientId)));
  routes.patch('/:id/read', (_request, response) => {
    write(response.locals.recipientId);
    response.sendStatus(204);
  });
  const app = createApp(async () => {}, { introspect: createIdentityClient(identity, trustedKey), routes });
  const base = await listen(t, app);
  const url = base + '/api/v1/notifications';

  const live = await fetch(base + '/health/live');
  assert.equal(live.status, 200);
  await live.json();

  for (const badHeaders of [{ 'X-User-ID': forged }, { Authorization: 'Basic credentials' }]) {
    const response = await fetch(url, { headers: badHeaders });
    assert.equal(response.status, 401);
    assert.equal((await response.json()).error.code, 'AUTH_REQUIRED');
  }
  assert.equal(calls.length, 0);
  assert.equal(read.mock.callCount(), 0);

  const allowed = await fetch(url + '?recipient_id=' + forged, { headers });
  assert.equal(allowed.status, 200);
  assert.equal(await allowed.json(), 'unread');
  assert.deepEqual(read.mock.calls[0].arguments, [recipient]);
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].url, '/internal/v1/auth/introspect');
  assert.equal(calls[0].headers.authorization, headers.Authorization);
  assert.equal(calls[0].headers['x-request-id'], requestId);
  assert.equal(calls[0].headers['x-service-key'], trustedKey);
  assert.equal(calls[0].headers['x-user-id'], undefined);

  for (const [upstreamStatus, upstreamCode, publicStatus, publicCode] of [
    [401, 'SESSION_INVALID', 401, 'SESSION_INVALID'],
    [401, 'SERVICE_CREDENTIAL_INVALID', 503, 'DEPENDENCY_UNAVAILABLE'],
    [503, 'DEPENDENCY_UNAVAILABLE', 503, 'DEPENDENCY_UNAVAILABLE'],
  ]) {
    status = upstreamStatus;
    code = upstreamCode;
    for (const [method, path] of [['GET', url], ['PATCH', url + '/' + forged + '/read']]) {
      const response = await fetch(path, { method, headers });
      assert.equal(response.status, publicStatus);
      assert.equal(response.headers.get('cache-control'), 'no-store');
      assert.equal(response.headers.get('x-request-id'), requestId);
      assert.equal(response.headers.get('retry-after'), publicStatus === 503 ? '1' : null);
      const { error } = await response.json();
      assert.equal(error.code, publicCode);
      assert.equal(error.request_id, requestId);
      assert.deepEqual(error.details, []);
      assert.ok(!error.message.includes('private diagnostic'));
    }
  }
  assert.equal(calls.length, 9);
  assert.equal(read.mock.callCount(), 1);
  assert.equal(write.mock.callCount(), 0);
  assert.equal(data.get(recipient), 'unread');
  assert.equal(data.get(forged), 'other recipient');
});
