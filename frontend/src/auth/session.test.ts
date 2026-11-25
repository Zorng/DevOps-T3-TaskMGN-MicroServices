import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApiClient } from '../api/client';
import { TEST_API_BASE_URL } from '../test/handlers';
import { server } from '../test/server';
import { SessionStore, type User } from './session';

const HOUR = 3_600_000;
const TOKEN = 'secret-token-0123456789-0123456789-0123456789';
const USER: User = {
  id: '00000000-0000-4000-8000-000000000001',
  display_name: 'Example Owner',
  email: 'owner@example.com',
  created_at: '2026-11-14T10:00:00Z',
};

function makeSession(expiresInMs = 8 * HOUR, token = TOKEN) {
  return {
    access_token: token,
    expires_at: new Date(Date.now() + expiresInMs).toISOString(),
    user: USER,
  };
}

describe('SessionStore', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-24T00:00:00Z'));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts signed out', () => {
    const store = new SessionStore();
    expect(store.getSnapshot()).toEqual({ status: 'signed_out', user: null, expiresAt: null });
    expect(store.getToken()).toBeNull();
  });

  it('signs in with the token available to the API client only', () => {
    const store = new SessionStore();
    const session = makeSession();
    store.signIn(session);

    expect(store.getToken()).toBe(TOKEN);
    expect(store.getSnapshot()).toEqual({
      status: 'signed_in',
      user: USER,
      expiresAt: session.expires_at,
    });
    expect(JSON.stringify(store.getSnapshot())).not.toContain(TOKEN);
  });

  it('never persists the token in browser storage', () => {
    const store = new SessionStore();
    store.signIn(makeSession());
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe('');
  });

  it('clears everything on sign out and notifies once even if called repeatedly', () => {
    const store = new SessionStore();
    const listener = vi.fn();
    store.signIn(makeSession());
    store.subscribe(listener);

    store.signOut();
    store.signOut();

    expect(store.getToken()).toBeNull();
    expect(store.getSnapshot().status).toBe('signed_out');
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('notifies subscribers and keeps the snapshot stable between changes', () => {
    const store = new SessionStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);

    const before = store.getSnapshot();
    expect(store.getSnapshot()).toBe(before);

    store.signIn(makeSession());
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot()).not.toBe(before);

    unsubscribe();
    store.signOut();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('signs out automatically when the session expires', () => {
    const store = new SessionStore();
    const listener = vi.fn();
    store.signIn(makeSession(8 * HOUR));
    store.subscribe(listener);

    vi.advanceTimersByTime(8 * HOUR - 1000);
    expect(store.getSnapshot().status).toBe('signed_in');

    vi.advanceTimersByTime(1000);
    expect(store.getSnapshot().status).toBe('signed_out');
    expect(store.getToken()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('refuses to hand out an expired token even if the timer has not fired', () => {
    const store = new SessionStore();
    store.signIn(makeSession(HOUR));
    vi.setSystemTime(Date.now() + 2 * HOUR); // clock jumped (e.g. the computer slept)
    expect(store.getToken()).toBeNull();
    expect(store.getSnapshot().status).toBe('signed_out');
  });

  it.each([
    ['an empty token', { ...makeSession(), access_token: '' }],
    ['an invalid expiry', { ...makeSession(), expires_at: 'not a date' }],
    ['an expiry in the past', { ...makeSession(), expires_at: '2026-11-23T00:00:00Z' }],
  ])('rejects %s and stays signed out', (_name, session) => {
    const store = new SessionStore();
    expect(() => store.signIn(session)).toThrow();
    expect(store.getSnapshot().status).toBe('signed_out');
    expect(store.getToken()).toBeNull();
  });

  it('replaces an earlier session and its expiry timer', () => {
    const store = new SessionStore();
    store.signIn(makeSession(HOUR, 'first-token-0123456789-0123456789-01234'));
    store.signIn(makeSession(8 * HOUR, TOKEN));

    vi.advanceTimersByTime(2 * HOUR); // the first session's timer would have fired by now
    expect(store.getSnapshot().status).toBe('signed_in');
    expect(store.getToken()).toBe(TOKEN);
  });

  it('updates the profile while signed in and ignores it while signed out', () => {
    const store = new SessionStore();
    store.setUser({ ...USER, display_name: 'Ignored' });
    expect(store.getSnapshot().user).toBeNull();

    store.signIn(makeSession());
    store.setUser({ ...USER, display_name: 'Renamed' });
    expect(store.getSnapshot().user?.display_name).toBe('Renamed');
  });
});

describe('SessionStore with the API client', () => {
  it('sends the stored token and signs out when the gateway answers 401', async () => {
    const store = new SessionStore();
    store.signIn(makeSession());
    const client = createApiClient({
      baseUrl: TEST_API_BASE_URL,
      getToken: () => store.getToken(),
      onUnauthorized: () => store.signOut(),
    });

    let authorization: string | null = null;
    server.use(
      http.get(`${TEST_API_BASE_URL}/api/v1/me`, ({ request }) => {
        authorization = request.headers.get('authorization');
        return HttpResponse.json(
          { error: { code: 'SESSION_INVALID', message: 'm', request_id: 'r', details: [] } },
          { status: 401 },
        );
      }),
    );

    await expect(client.get('/me')).rejects.toMatchObject({ status: 401, code: 'SESSION_INVALID' });
    expect(authorization).toBe(`Bearer ${TOKEN}`);
    expect(store.getSnapshot().status).toBe('signed_out');
    expect(store.getToken()).toBeNull();
  });
});