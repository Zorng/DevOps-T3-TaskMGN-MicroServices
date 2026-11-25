import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { TEST_API_BASE_URL } from '../test/handlers';
import { server } from '../test/server';
import { createApiClient } from './client';
import { ApiError } from './errors';
import {
    CreateAttempt,
    canRetryWithSameKey,
    deleteWithVersion,
    isAmbiguousOutcome,
    isVersionConflict,
    updateWithVersion,
} from './mutations';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const API = `${TEST_API_BASE_URL}/api/v1`;
const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'tok' });

function errorBody(code: string) {
    return { error: { code, message: 'm', request_id: 'req-1', details: [] } };
}

describe('CreateAttempt', () => {
    it('sends the body with a UUID idempotency key and returns the created resource', async () => {
        let key: string | null = null;
        let body: unknown;
        server.use(
            http.post(`${API}/workspaces`, async ({ request }) => {
                key = request.headers.get('idempotency-key');
                body = await request.json();
                return HttpResponse.json({ id: 'w1' }, { status: 201, headers: { ETag: '"1"' } });
            }),
        );

        const attempt = new CreateAttempt<{ id: string }>(client, '/workspaces', { name: 'Team' });
        const result = await attempt.send();

        expect(attempt.idempotencyKey).toMatch(UUID);
        expect(key).toBe(attempt.idempotencyKey);
        expect(body).toEqual({ name: 'Team' });
        expect(result.data).toEqual({ id: 'w1' });
        expect(result.etag).toBe('"1"');
        expect(attempt.state).toBe('succeeded');
    });

    it('gives every attempt its own key', () => {
        const a = new CreateAttempt(client, '/workspaces', {});
        const b = new CreateAttempt(client, '/workspaces', {});
        expect(a.idempotencyKey).not.toBe(b.idempotencyKey);
    });

    it('does not send again after success', async () => {
        let calls = 0;
        server.use(
            http.post(`${API}/workspaces`, () => {
                calls += 1;
                return HttpResponse.json({ id: 'w1' }, { status: 201 });
            }),
        );
        const attempt = new CreateAttempt(client, '/workspaces', { name: 'Team' });
        await attempt.send();
        await attempt.send();
        expect(calls).toBe(1);
    });

    it('shares one request between concurrent sends', async () => {
        let calls = 0;
        server.use(
            http.post(`${API}/workspaces`, () => {
                calls += 1;
                return HttpResponse.json({ id: 'w1' }, { status: 201 });
            }),
        );
        const attempt = new CreateAttempt(client, '/workspaces', { name: 'Team' });
        await Promise.all([attempt.send(), attempt.send()]);
        expect(calls).toBe(1);
    });

    it('reuses the same key when retrying after a network failure', async () => {
        const keys: (string | null)[] = [];
        server.use(
            http.post(`${API}/boards`, ({ request }) => {
                keys.push(request.headers.get('idempotency-key'));
                if (keys.length === 1) return HttpResponse.error();
                return HttpResponse.json({ id: 'b1' }, { status: 201 });
            }),
        );

        const attempt = new CreateAttempt(client, '/boards', { name: 'Todo' });
        await expect(attempt.send()).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
        expect(attempt.state).toBe('retryable');

        await attempt.send();
        expect(attempt.state).toBe('succeeded');
        expect(keys).toHaveLength(2);
        expect(keys[0]).toBe(keys[1]);
    });

    it.each([
        [500, 'INTERNAL_ERROR'],
        [503, 'DEPENDENCY_UNAVAILABLE'],
        [409, 'IDEMPOTENCY_IN_PROGRESS'],
        [429, 'RATE_LIMITED'],
    ])('keeps the key and allows a retry after %i %s', async (status, code) => {
        const keys: (string | null)[] = [];
        server.use(
            http.post(`${API}/tasks`, ({ request }) => {
                keys.push(request.headers.get('idempotency-key'));
                if (keys.length === 1) return HttpResponse.json(errorBody(code), { status });
                return HttpResponse.json({ id: 't1' }, { status: 201 });
            }),
        );

        const attempt = new CreateAttempt(client, '/tasks', { title: 'x' });
        await expect(attempt.send()).rejects.toMatchObject({ status, code });
        expect(attempt.state).toBe('retryable');

        await attempt.send();
        expect(keys[0]).toBe(keys[1]);
    });

    it('is final after a validation rejection and a new attempt gets a new key', async () => {
        let calls = 0;
        server.use(
            http.post(`${API}/tasks`, () => {
                calls += 1;
                return HttpResponse.json(errorBody('VALIDATION_FAILED'), { status: 422 });
            }),
        );

        const attempt = new CreateAttempt(client, '/tasks', { title: '' });
        await expect(attempt.send()).rejects.toMatchObject({ status: 422 });
        expect(attempt.state).toBe('rejected');

        await expect(attempt.send()).rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
        expect(calls).toBe(1);

        const corrected = new CreateAttempt(client, '/tasks', { title: 'Fixed' });
        expect(corrected.idempotencyKey).not.toBe(attempt.idempotencyKey);
    });
});

describe('error classifiers', () => {
    const make = (status: number, code: string) => new ApiError({ status, code, message: 'm' });

    it('treats no response, 5xx and IDEMPOTENCY_IN_PROGRESS as ambiguous', () => {
        expect(isAmbiguousOutcome(make(0, 'NETWORK_ERROR'))).toBe(true);
        expect(isAmbiguousOutcome(make(503, 'DEPENDENCY_UNAVAILABLE'))).toBe(true);
        expect(isAmbiguousOutcome(make(409, 'IDEMPOTENCY_IN_PROGRESS'))).toBe(true);
    });

    it('does not treat definitive rejections as ambiguous', () => {
        expect(isAmbiguousOutcome(make(422, 'VALIDATION_FAILED'))).toBe(false);
        expect(isAmbiguousOutcome(make(403, 'FORBIDDEN'))).toBe(false);
        expect(isAmbiguousOutcome(make(409, 'IDEMPOTENCY_CONFLICT'))).toBe(false);
        expect(isAmbiguousOutcome(new Error('x'))).toBe(false);
    });

    it('allows a same-key retry for 429 but not for 422', () => {
        expect(canRetryWithSameKey(make(429, 'RATE_LIMITED'))).toBe(true);
        expect(canRetryWithSameKey(make(422, 'VALIDATION_FAILED'))).toBe(false);
    });

    it('recognises a version conflict', () => {
        expect(isVersionConflict(make(412, 'VERSION_CONFLICT'))).toBe(true);
        expect(isVersionConflict(make(404, 'NOT_FOUND'))).toBe(false);
    });
});

describe('versioned updates', () => {
    it('sends If-Match on PATCH and DELETE', async () => {
        const seen: (string | null)[] = [];
        server.use(
            http.patch(`${API}/tasks/t1`, ({ request }) => {
                seen.push(request.headers.get('if-match'));
                return HttpResponse.json({ id: 't1' }, { headers: { ETag: '"5"' } });
            }),
            http.delete(`${API}/tasks/t1`, ({ request }) => {
                seen.push(request.headers.get('if-match'));
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const updated = await updateWithVersion(client, '/tasks/t1', { title: 'New' }, '"4"');
        await deleteWithVersion(client, '/tasks/t1', '"5"');
        expect(updated.etag).toBe('"5"');
        expect(seen).toEqual(['"4"', '"5"']);
    });

    it('surfaces a 412 conflict once, without retrying', async () => {
        let calls = 0;
        server.use(
            http.patch(`${API}/tasks/t1`, () => {
                calls += 1;
                return HttpResponse.json(errorBody('VERSION_CONFLICT'), { status: 412 });
            }),
        );
        const failure = await updateWithVersion(client, '/tasks/t1', { title: 'x' }, '"1"').catch((e: unknown) => e);
        expect(isVersionConflict(failure)).toBe(true);
        expect(calls).toBe(1);
    });
});