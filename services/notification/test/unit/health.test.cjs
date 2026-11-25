const assert = require('node:assert/strict');
const { test } = require('node:test');
const { liveness, readiness } = require('../../dist/health.js');
const { checkDependencies } = require('../../dist/dependencies.js');

test('liveness is independent of external dependencies', () => {
  assert.deepEqual(liveness(), { status: 'ok' });
});

test('readiness translates dependency results into safe probe responses', async () => {
  assert.deepEqual(await readiness(async () => {}), { code: 200, body: { status: 'ok' } });
  assert.deepEqual(await readiness(async () => { throw new Error('private'); }), {
    code: 503, body: { status: 'unavailable' },
  });
});

test('the dependency check requires both PostgreSQL and Identity and forwards the request ID', async (t) => {
  let identityStatus = 200;
  const requestId = 'c1e30e7f-a037-4cb0-9331-04ee57d821a6';
  const identity = t.mock.method(globalThis, 'fetch', async (_url, options) => {
    assert.equal(options.headers['X-Request-ID'], requestId);
    return new Response(null, { status: identityStatus });
  });
  const pool = { query: t.mock.fn(async () => {}) };
  const url = 'http://identity-workspace:8000/health/ready';
  await checkDependencies(pool, url, requestId);
  assert.equal(identity.mock.calls[0].arguments[0], url);
  assert.match(pool.query.mock.calls[0].arguments[0], /notification_service\.notifications/);
  identityStatus = 503;
  await assert.rejects(checkDependencies(pool, url, requestId), /Identity is unavailable/);
  identityStatus = 200;
  await assert.rejects(checkDependencies({
    query: async () => { throw new Error('database unavailable'); },
  }, url, requestId), /database unavailable/);
});
