const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { test } = require('node:test');
const { createApp } = require('../../dist/app.js');
const { listen } = require('../http.cjs');

test('readiness returns contract status and headers through failure and recovery', async (t) => {
  let available = false;
  const id = randomUUID();
  const check = t.mock.fn(async () => {
    if (!available) throw new Error('private error');
  });
  const base = await listen(t, createApp(check));

  for (const [status, body] of [[503, 'unavailable'], [200, 'ok']]) {
    const response = await fetch(`${base}/health/ready`, { headers: { 'X-Request-ID': id } });
    assert.equal(response.status, status);
    assert.equal(response.headers.get('x-request-id'), id);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.deepEqual(await response.json(), { status: body });
    available = true;
  }
  assert.deepEqual(check.mock.calls.map((call) => call.arguments), [[id], [id]]);
});

test('input limits and routing failures return safe correlated error envelopes', async (t) => {
  const base = await listen(t, createApp(async () => {}));
  const boundary = JSON.stringify('é'.repeat(32767));
  const cases = [
    ['/missing', boundary, 'application/json', 404, 'NOT_FOUND'],
    ['/missing', boundary + ' ', 'application/json', 413, 'PAYLOAD_TOO_LARGE'],
    ['/missing', '{', 'application/json', 400, 'MALFORMED_REQUEST'],
    ['/missing', '{}', 'text/plain', 415, 'UNSUPPORTED_MEDIA_TYPE'],
    ['/health/live?extra=1', undefined, undefined, 400, 'MALFORMED_REQUEST'],
  ];
  for (const [path, body, contentType, status, code] of cases) {
    const response = await fetch(base + path, body === undefined ? undefined : {
      method: 'POST', headers: { 'Content-Type': contentType }, body,
    });
    assert.equal(response.status, status);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    const { error } = await response.json();
    assert.equal(error.code, code);
    assert.equal(error.request_id, response.headers.get('x-request-id'));
    assert.deepEqual(error.details, []);
  }
});

test('metrics report completed traffic and server errors while logs omit secrets', async (t) => {
  const secret = 'test-private-credential';
  const logs = t.mock.method(console, 'log', () => {});
  const base = await listen(t, createApp(async () => { throw new Error(secret); }));
  const live = await fetch(`${base}/health/live?token=${secret}`, {
    headers: { Authorization: `Bearer ${secret}`, 'X-Request-ID': secret },
  });
  await live.text();
  const ready = await fetch(`${base}/health/ready`);
  await ready.text();

  const metrics = await fetch(`${base}/metrics`);
  assert.equal(metrics.status, 200);
  const body = await metrics.text();
  assert.match(body, /^notification_http_requests_total 2$/m);
  assert.match(body, /^notification_http_server_errors_total 1$/m);
  assert.match(body, /^# TYPE notification_http_request_duration_seconds histogram$/m);
  assert.match(body, /^notification_http_request_duration_seconds_bucket\{le="\+Inf"\} 2$/m);
  assert.match(body, /^notification_http_request_duration_seconds_count 2$/m);
  assert.match(body, /^notification_http_request_duration_seconds_sum [\d.e+-]+$/m);
  assert.equal(logs.mock.callCount(), 3);
  for (const call of logs.mock.calls) {
    const line = call.arguments[0];
    assert.ok(!line.includes(secret));
    const entry = JSON.parse(line);
    assert.equal(entry.event, 'request_completed');
    assert.equal(entry.level, entry.status >= 500 ? 'error' : entry.status >= 400 ? 'warn' : 'info');
  }
});
