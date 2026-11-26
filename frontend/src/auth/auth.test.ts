import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';
import { createApiClient } from '../api/client';
import { TEST_API_BASE_URL } from '../test/handlers';
import { server } from '../test/server';
import { createChallenge, exchangeCredential, fetchMe, logout } from './authApi';
import { AuthService } from './authService';
import { SessionStore } from './session';

const API = `${TEST_API_BASE_URL}/api/v1`;
const USER = {
    id: '00000000-0000-4000-8000-000000000001',
    display_name: 'Example Owner',
    email: 'owner@example.com',
    created_at: '2026-11-14T10:00:00Z',
};
const TOKEN = 'synthetic-session-token-not-a-real-credential';

function challengeBody(expiresInMs = 5 * 60_000) {
    return {
        challenge_id: '00000000-0000-4000-8000-000000000013',
        nonce: 'synthetic-nonce-not-for-use-in-production-0001',
        expires_at: new Date(Date.now() + expiresInMs).toISOString(),
    };
}

function sessionBody() {
    return {
        access_token: TOKEN,
        token_type: 'Bearer',
        expires_at: new Date(Date.now() + 8 * 3_600_000).toISOString(),
        user: USER,
    };
}

function errorBody(code: string) {
    return { error: { code, message: 'm', request_id: 'req-1', details: [] } };
}

const stores: SessionStore[] = [];
afterEach(() => {
    for (const store of stores) store.signOut(); // cancels each store's expiry timer
    stores.length = 0;
});

function makeService() {
    const store = new SessionStore();
    stores.push(store);
    const client = createApiClient({
        baseUrl: TEST_API_BASE_URL,
        getToken: () => store.getToken(),
        onUnauthorized: () => store.signOut(),
    });
    return { store, client, service: new AuthService(client, store) };
}

describe('auth API calls', () => {
    it('creates a challenge with no body and no credentials', async () => {
        const seen: { auth?: string | null; contentType?: string | null; body?: string } = {};
        server.use(
            http.post(`${API}/auth/google/challenges`, async ({ request }) => {
                seen.auth = request.headers.get('authorization');
                seen.contentType = request.headers.get('content-type');
                seen.body = await request.text();
                return HttpResponse.json(challengeBody(), { status: 201 });
            }),
        );
        const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'stale-token' });

        const challenge = await createChallenge(client);

        expect(challenge.nonce).toBe('synthetic-nonce-not-for-use-in-production-0001');
        expect(seen).toEqual({ auth: null, contentType: null, body: '' });
    });

    it('rejects a malformed challenge response', async () => {
        server.use(http.post(`${API}/auth/google/challenges`, () => HttpResponse.json({ challenge_id: 'x' }, { status: 201 })));
        const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => null });
        await expect(createChallenge(client)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
    });

    it('exchanges a Google credential for a session without sending a bearer token', async () => {
        const seen: { auth?: string | null; body?: unknown } = {};
        server.use(
            http.post(`${API}/auth/google/exchange`, async ({ request }) => {
                seen.auth = request.headers.get('authorization');
                seen.body = await request.json();
                return HttpResponse.json(sessionBody());
            }),
        );
        const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'stale-token' });

        const session = await exchangeCredential(client, { challengeId: 'c1', idToken: 'google-id-token' });

        expect(session.access_token).toBe(TOKEN);
        expect(session.user.email).toBe('owner@example.com');
        expect(seen.auth).toBeNull();
        expect(seen.body).toEqual({ challenge_id: 'c1', id_token: 'google-id-token' });
    });

    it.each([
        ['a token type other than Bearer', { ...sessionBody(), token_type: 'Basic' }],
        ['a user without an email', { ...sessionBody(), user: { id: 'u', display_name: 'x' } }],
        ['no token', { ...sessionBody(), access_token: '' }],
    ])('rejects a session response with %s', async (_name, body) => {
        server.use(http.post(`${API}/auth/google/exchange`, () => HttpResponse.json(body)));
        const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => null });
        await expect(exchangeCredential(client, { challengeId: 'c', idToken: 't' })).rejects.toMatchObject({
            code: 'INVALID_RESPONSE',
        });
    });

    it('loads the current user and owned workspace count with the bearer token', async () => {
        let authorization: string | null = null;
        server.use(
            http.get(`${API}/me`, ({ request }) => {
                authorization = request.headers.get('authorization');
                return HttpResponse.json({ user: USER, owned_workspace_count: 3 });
            }),
        );
        const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => TOKEN });
        const me = await fetchMe(client);
        expect(me.owned_workspace_count).toBe(3);
        expect(authorization).toBe(`Bearer ${TOKEN}`);
    });

    it('logs out with the bearer token and no body', async () => {
        const seen: { auth?: string | null; body?: string } = {};
        server.use(
            http.post(`${API}/auth/logout`, async ({ request }) => {
                seen.auth = request.headers.get('authorization');
                seen.body = await request.text();
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => TOKEN });
        await logout(client);
        expect(seen).toEqual({ auth: `Bearer ${TOKEN}`, body: '' });
    });

    it('maps a denied origin and an unavailable service to contract errors', async () => {
        const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => null });
        server.use(
            http.post(`${API}/auth/google/challenges`, () => HttpResponse.json(errorBody('ORIGIN_NOT_ALLOWED'), { status: 403 })),
        );
        await expect(createChallenge(client)).rejects.toMatchObject({ status: 403, code: 'ORIGIN_NOT_ALLOWED' });

        server.use(
            http.post(`${API}/auth/google/challenges`, () =>
                HttpResponse.json(errorBody('DEPENDENCY_UNAVAILABLE'), { status: 503, headers: { 'Retry-After': '3' } }),
            ),
        );
        await expect(createChallenge(client)).rejects.toMatchObject({
            status: 503,
            code: 'DEPENDENCY_UNAVAILABLE',
            retryAfterSeconds: 3,
        });
    });
});

describe('AuthService sign-in', () => {
    it('signs in with a valid challenge and credential', async () => {
        server.use(http.post(`${API}/auth/google/exchange`, () => HttpResponse.json(sessionBody())));
        const { service, store } = makeService();

        const user = await service.completeSignIn(challengeBody(), 'google-id-token');

        expect(user.display_name).toBe('Example Owner');
        expect(store.getSnapshot().status).toBe('signed_in');
        expect(store.getToken()).toBe(TOKEN);
    });

    it('does not exchange an expired challenge', async () => {
        let calls = 0;
        server.use(
            http.post(`${API}/auth/google/exchange`, () => {
                calls += 1;
                return HttpResponse.json(sessionBody());
            }),
        );
        const { service, store } = makeService();

        await expect(service.completeSignIn(challengeBody(-1000), 't')).rejects.toMatchObject({
            code: 'AUTH_CHALLENGE_INVALID',
        });
        expect(calls).toBe(0);
        expect(store.getSnapshot().status).toBe('signed_out');
    });

    it.each([
        ['AUTH_CHALLENGE_INVALID', 401],
        ['GOOGLE_TOKEN_INVALID', 401],
        ['ORIGIN_NOT_ALLOWED', 403],
        ['DEPENDENCY_UNAVAILABLE', 503],
    ])('stays signed out and does not retry after %s', async (code, status) => {
        let calls = 0;
        server.use(
            http.post(`${API}/auth/google/exchange`, () => {
                calls += 1;
                return HttpResponse.json(errorBody(code), { status });
            }),
        );
        const { service, store } = makeService();

        await expect(service.completeSignIn(challengeBody(), 't')).rejects.toMatchObject({ code, status });
        expect(calls).toBe(1);
        expect(store.getSnapshot().status).toBe('signed_out');
    });

    it('shares one exchange when the Google callback fires twice for the same challenge', async () => {
        let calls = 0;
        server.use(
            http.post(`${API}/auth/google/exchange`, () => {
                calls += 1;
                return HttpResponse.json(sessionBody());
            }),
        );
        const { service } = makeService();
        const challenge = challengeBody();

        await Promise.all([service.completeSignIn(challenge, 't'), service.completeSignIn(challenge, 't')]);
        expect(calls).toBe(1);
    });

    it('starts sign-in by creating a challenge', async () => {
        server.use(http.post(`${API}/auth/google/challenges`, () => HttpResponse.json(challengeBody(), { status: 201 })));
        const { service } = makeService();
        const challenge = await service.startSignIn();
        expect(challenge.challenge_id).toBe('00000000-0000-4000-8000-000000000013');
    });
});

describe('AuthService session', () => {
    async function signedIn() {
        server.use(http.post(`${API}/auth/google/exchange`, () => HttpResponse.json(sessionBody())));
        const context = makeService();
        await context.service.completeSignIn(challengeBody(), 't');
        return context;
    }

    it('loads the profile and refreshes the stored user', async () => {
        const { service, store } = await signedIn();
        server.use(
            http.get(`${API}/me`, () =>
                HttpResponse.json({ user: { ...USER, display_name: 'Renamed' }, owned_workspace_count: 2 }),
            ),
        );
        const me = await service.loadMe();
        expect(me.owned_workspace_count).toBe(2);
        expect(store.getSnapshot().user?.display_name).toBe('Renamed');
    });

    it('signs out locally when the gateway answers 401 to GET /me', async () => {
        const { service, store } = await signedIn();
        server.use(http.get(`${API}/me`, () => HttpResponse.json(errorBody('SESSION_INVALID'), { status: 401 })));
        await expect(service.loadMe()).rejects.toMatchObject({ code: 'SESSION_INVALID' });
        expect(store.getSnapshot().status).toBe('signed_out');
    });

    it('revokes the session on logout and clears local state', async () => {
        const { service, store } = await signedIn();
        let authorization: string | null = null;
        server.use(
            http.post(`${API}/auth/logout`, ({ request }) => {
                authorization = request.headers.get('authorization');
                return new HttpResponse(null, { status: 204 });
            }),
        );
        expect(await service.signOut()).toBe('revoked');
        expect(authorization).toBe(`Bearer ${TOKEN}`);
        expect(store.getToken()).toBeNull();
    });

    it('clears local state even when logout returns 401', async () => {
        const { service, store } = await signedIn();
        server.use(http.post(`${API}/auth/logout`, () => HttpResponse.json(errorBody('SESSION_INVALID'), { status: 401 })));
        expect(await service.signOut()).toBe('revoked');
        expect(store.getSnapshot().status).toBe('signed_out');
    });

    it.each([
        ['an unavailable service', () => HttpResponse.json(errorBody('DEPENDENCY_UNAVAILABLE'), { status: 503 })],
        ['a network failure', () => HttpResponse.error()],
    ])('clears local state and reports "not confirmed" after %s', async (_name, respond) => {
        const { service, store } = await signedIn();
        server.use(http.post(`${API}/auth/logout`, respond));
        expect(await service.signOut()).toBe('not_confirmed');
        expect(store.getSnapshot().status).toBe('signed_out');
    });

    it('sends no request when there is no session to log out', async () => {
        let calls = 0;
        server.use(
            http.post(`${API}/auth/logout`, () => {
                calls += 1;
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const { service } = makeService();
        expect(await service.signOut()).toBe('revoked');
        expect(calls).toBe(0);
    });
});