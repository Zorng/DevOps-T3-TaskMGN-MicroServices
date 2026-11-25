import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { server } from '../test/server';
import { TEST_API_BASE_URL } from '../test/handlers';
import { createApiClient, type ApiClientOptions } from './client';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const API = `${TEST_API_BASE_URL}/api/v1`;

function makeClient(overrides: Partial<ApiClientOptions> = {}) {
    return createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'tok-123', ...overrides });
}

function errorBody(code: string, message = 'm') {
    return { error: { code, message, request_id: 'req-1', details: [] } };
}

describe('request headers', () => {
    it('sends bearer and request ID, and never sends service or identity headers', async () => {
        let seen: Headers | undefined;
        server.use(
            http.get(`${API}/me`, ({ request }) => {
                seen = request.headers;
                return HttpResponse.json({ id: 'u1' }, { headers: { ETag: '"3"', 'X-Request-ID': 'server-id' } });
            }),
        );

        const result = await makeClient().get<{ id: string }>('/me');

        expect(result.data).toEqual({ id: 'u1' });
        expect(result.etag).toBe('"3"');
        expect(result.requestId).toBe('server-id');
        expect(seen?.get('authorization')).toBe('Bearer tok-123');
        expect(seen?.get('x-request-id')).toMatch(UUID);
        expect(seen?.get('content-type')).toBeNull();
        expect(seen?.get('x-service-key')).toBeNull();
        expect(seen?.get('x-user-id')).toBeNull();
        expect(seen?.get('x-user-role')).toBeNull();
    });

    it('uses a fresh request ID for every call', async () => {
        const ids: (string | null)[] = [];
        server.use(
            http.get(`${API}/me`, ({ request }) => {
                ids.push(request.headers.get('x-request-id'));
                return HttpResponse.json({});
            }),
        );
        const client = makeClient();
        await client.get('/me');
        await client.get('/me');
        expect(ids[0]).not.toBe(ids[1]);
    });

    it('omits Authorization when signed out or when auth is disabled', async () => {
        const seen: (string | null)[] = [];
        server.use(
            http.get(`${API}/me`, ({ request }) => {
                seen.push(request.headers.get('authorization'));
                return HttpResponse.json({});
            }),
        );
        await makeClient({ getToken: () => null }).get('/me');
        await makeClient().get('/me', { auth: false });
        expect(seen).toEqual([null, null]);
    });

    it('sends a JSON body with Idempotency-Key on a create', async () => {
        let seen: Headers | undefined;
        let body: unknown;
        server.use(
            http.post(`${API}/workspaces`, async ({ request }) => {
                seen = request.headers;
                body = await request.json();
                return HttpResponse.json({ id: 'w1' }, { status: 201, headers: { ETag: '"1"' } });
            }),
        );

        const result = await makeClient().post('/workspaces', {
            body: { name: 'Team' },
            idempotencyKey: 'key-1',
        });

        expect(result.status).toBe(201);
        expect(result.etag).toBe('"1"');
        expect(body).toEqual({ name: 'Team' });
        expect(seen?.get('content-type')).toBe('application/json');
        expect(seen?.get('idempotency-key')).toBe('key-1');
    });

    it('sends If-Match exactly as given on a PATCH', async () => {
        let ifMatch: string | null = null;
        server.use(
            http.patch(`${API}/workspaces/w1`, ({ request }) => {
                ifMatch = request.headers.get('if-match');
                return HttpResponse.json({ id: 'w1' }, { headers: { ETag: '"4"' } });
            }),
        );
        const result = await makeClient().patch('/workspaces/w1', { body: { name: 'New' }, ifMatch: '"3"' });
        expect(ifMatch).toBe('"3"');
        expect(result.etag).toBe('"4"');
    });
});

describe('URLs', () => {
    it('adds the /api/v1 prefix and encodes query parameters, skipping undefined', async () => {
        let url = '';
        server.use(
            http.get(`${API}/notifications`, ({ request }) => {
                url = request.url;
                return HttpResponse.json({ items: [], next_cursor: null });
            }),
        );
        await makeClient().get('/notifications', {
            query: { limit: 20, unread_only: true, cursor: undefined, q: 'a b' },
        });
        const parsed = new URL(url);
        expect(parsed.searchParams.get('limit')).toBe('20');
        expect(parsed.searchParams.get('unread_only')).toBe('true');
        expect(parsed.searchParams.get('q')).toBe('a b');
        expect(parsed.searchParams.has('cursor')).toBe(false);
    });

    it('rejects a path without a leading slash', async () => {
        await expect(makeClient().get('me')).rejects.toThrow('must start with');
    });
});

describe('responses', () => {
    it('returns undefined data for 204', async () => {
        server.use(http.delete(`${API}/workspaces/w1`, () => new HttpResponse(null, { status: 204 })));
        const result = await makeClient().delete('/workspaces/w1', { ifMatch: '"1"' });
        expect(result.status).toBe(204);
        expect(result.data).toBeUndefined();
    });

    it('maps a contract error to ApiError', async () => {
        server.use(http.get(`${API}/me`, () => HttpResponse.json(errorBody('NOT_FOUND'), { status: 404 })));
        await expect(makeClient().get('/me')).rejects.toMatchObject({
            name: 'ApiError',
            status: 404,
            code: 'NOT_FOUND',
            requestId: 'req-1',
        });
    });

    it('surfaces a version conflict', async () => {
        server.use(
            http.patch(`${API}/tasks/t1`, () => HttpResponse.json(errorBody('VERSION_CONFLICT'), { status: 412 })),
        );
        await expect(makeClient().patch('/tasks/t1', { body: { title: 'x' }, ifMatch: '"1"' })).rejects.toMatchObject({
            status: 412,
            code: 'VERSION_CONFLICT',
        });
    });

    it('reports an unavailable service with Retry-After', async () => {
        server.use(
            http.get(`${API}/me`, () =>
                HttpResponse.json(errorBody('DEPENDENCY_UNAVAILABLE'), { status: 503, headers: { 'Retry-After': '7' } }),
            ),
        );
        await expect(makeClient().get('/me')).rejects.toMatchObject({
            status: 503,
            code: 'DEPENDENCY_UNAVAILABLE',
            retryAfterSeconds: 7,
        });
    });

    it('treats a 2xx body that is not JSON as INVALID_RESPONSE', async () => {
        server.use(http.get(`${API}/me`, () => new HttpResponse('<html></html>', { status: 200 })));
        await expect(makeClient().get('/me')).rejects.toMatchObject({ code: 'INVALID_RESPONSE', status: 200 });
    });

    it('reports a network failure as NETWORK_ERROR with status 0', async () => {
        server.use(http.get(`${API}/me`, () => HttpResponse.error()));
        await expect(makeClient().get('/me')).rejects.toMatchObject({ code: 'NETWORK_ERROR', status: 0 });
    });
});

describe('401 handling', () => {
    it('calls onUnauthorized once for an authenticated request and still throws', async () => {
        server.use(http.get(`${API}/me`, () => HttpResponse.json(errorBody('SESSION_INVALID'), { status: 401 })));
        const onUnauthorized = vi.fn();
        await expect(makeClient({ onUnauthorized }).get('/me')).rejects.toMatchObject({ code: 'SESSION_INVALID' });
        expect(onUnauthorized).toHaveBeenCalledTimes(1);
    });

    it('does not call onUnauthorized for sign-in calls made without a token', async () => {
        server.use(
            http.post(`${API}/auth/google/exchange`, () =>
                HttpResponse.json(errorBody('AUTH_CHALLENGE_INVALID'), { status: 401 }),
            ),
        );
        const onUnauthorized = vi.fn();
        await expect(
            makeClient({ onUnauthorized }).post('/auth/google/exchange', { auth: false, body: {} }),
        ).rejects.toMatchObject({ code: 'AUTH_CHALLENGE_INVALID' });
        expect(onUnauthorized).not.toHaveBeenCalled();
    });
});