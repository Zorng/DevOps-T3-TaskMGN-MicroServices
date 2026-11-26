const assert = require('node:assert/strict');
const { test } = require('node:test');
const { createIdentityClient } = require('../../dist/identity.js');

const origin = 'http://identity.test';
const bearer = 'Bearer synthetic-session';
const requestId = '2bbd833f-dbca-4580-9d50-c0b760403957';
const recipientId = 'f154adb7-a50c-42ca-9a98-d1a8eb1e4c11';
const key = 'synthetic-service-key';
const session = {
  user: { id: recipientId, display_name: 'Test User' },
  session_expires_at: '2099-01-01T00:00:00Z',
};

test('introspection forwards only the contracted inputs with a two-second timeout', async (t) => {
  const deadline = t.mock.method(AbortSignal, 'timeout');
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json(session));
  const introspect = createIdentityClient(origin, key);

  assert.deepEqual(await introspect(bearer, requestId), { kind: 'verified', recipientId });
  assert.equal(fetch.mock.callCount(), 1);
  const [url, options] = fetch.mock.calls[0].arguments;
  assert.equal(url.href, origin + '/internal/v1/auth/introspect');
  assert.equal(options.method, 'POST');
  assert.equal(options.redirect, 'manual');
  assert.equal(options.body, undefined);
  assert.deepEqual(options.headers, {
    Authorization: bearer,
    'X-Service-Key': key,
    'X-Request-ID': requestId,
  });
  assert.deepEqual(deadline.mock.calls[0].arguments, [2000]);
});

test('introspection retries transport errors or 503 at most once', async (t) => {
  let nextResponse;
  const fetch = t.mock.method(globalThis, 'fetch', async () => nextResponse());
  const introspect = createIdentityClient(origin, key);
  for (const fail of [
    () => { throw new DOMException('Deadline exceeded', 'TimeoutError'); },
    () => new Response(null, { status: 503 }),
  ]) {
    fetch.mock.resetCalls();
    nextResponse = fail;
    assert.deepEqual(await introspect(bearer, requestId), { kind: 'unavailable' });
    assert.equal(fetch.mock.callCount(), 2);

    fetch.mock.resetCalls();
    let first = true;
    nextResponse = () => {
      if (first) {
        first = false;
        return fail();
      }
      return Response.json(session);
    };
    assert.deepEqual(await introspect(bearer, requestId), { kind: 'verified', recipientId });
    assert.equal(fetch.mock.callCount(), 2);
  }
});

test('explicit failures and malformed replies fail closed without retry', async (t) => {
  let reply;
  const fetch = t.mock.method(globalThis, 'fetch', async () => reply);
  const introspect = createIdentityClient(origin, key);
  const cases = [
    [Response.json({ error: { code: 'SESSION_INVALID' } }, { status: 401 }), 'invalid'],
    [Response.json({ error: { code: 'SERVICE_CREDENTIAL_INVALID' } }, { status: 401 }), 'credential_rejected'],
    [new Response(null, { status: 403 }), 'credential_rejected'],
    [new Response(null, { status: 404 }), 'unavailable'],
    [new Response(null, { status: 500 }), 'unavailable'],
    [Response.redirect('https://other.test'), 'unavailable'],
    [Response.json({ user: { id: 'bogus' } }), 'unavailable'],
    [new Response('invalid JSON'), 'unavailable'],
    [Response.json({}, { status: 401 }), 'unavailable'],
  ];
  for (const [response, kind] of cases) {
    reply = response;
    fetch.mock.resetCalls();
    assert.deepEqual(await introspect(bearer, requestId), { kind });
    assert.equal(fetch.mock.callCount(), 1);
  }
});

test('missing credentials or an invalid Identity origin prevent outbound calls', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch');
  for (const [base, serviceKey] of [
    [undefined, key], [origin, undefined], [origin + '/path', key], ['http://secret@identity.test', key],
  ]) {
    assert.deepEqual(await createIdentityClient(base, serviceKey)(bearer, requestId), { kind: 'unavailable' });
  }
  assert.equal(fetch.mock.callCount(), 0);
});
