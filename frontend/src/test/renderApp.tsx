import { render } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router';
import { vi } from 'vitest';
import { App } from '../App';
import { AuthProvider, createAuthServices } from '../auth/AuthProvider';
import type { GoogleIdConfiguration, GoogleIdentity } from '../auth/gis';
import { TEST_API_BASE_URL } from './handlers';

export const API = `${TEST_API_BASE_URL}/api/v1`;
export const TOKEN = 'synthetic-session-token-not-a-real-credential';
export const CLIENT_ID = 'test-client.apps.googleusercontent.com';
export const USER = {
    id: '00000000-0000-4000-8000-000000000001',
    display_name: 'Example Owner',
    email: 'owner@example.com',
    created_at: '2026-11-14T10:00:00Z',
};

export function challenge(n = 1) {
    return {
        challenge_id: `00000000-0000-4000-8000-00000000001${n}`,
        nonce: `synthetic-nonce-${n}-not-for-use-in-production-0000`,
        expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
    };
}

export function sessionBody() {
    return {
        access_token: TOKEN,
        token_type: 'Bearer',
        expires_at: new Date(Date.now() + 8 * 3_600_000).toISOString(),
        user: USER,
    };
}

export function errorBody(code: string) {
    return { error: { code, message: 'm', request_id: 'req-1', details: [] } };
}

export const meHandler = (ownedWorkspaces = 1) =>
    http.get(`${API}/me`, () => HttpResponse.json({ user: USER, owned_workspace_count: ownedWorkspaces }));

export const challengeHandler = () =>
    http.post(`${API}/auth/google/challenges`, () => HttpResponse.json(challenge(1), { status: 201 }));

export function fakeGoogle() {
    return {
        initialize: vi.fn<(config: GoogleIdConfiguration) => void>(),
        renderButton: vi.fn<GoogleIdentity['renderButton']>(),
        disableAutoSelect: vi.fn<GoogleIdentity['disableAutoSelect']>(),
        cancel: vi.fn<GoogleIdentity['cancel']>(),
    };
}
export type FakeGoogle = ReturnType<typeof fakeGoogle>;

/** The configuration most recently passed to google.accounts.id.initialize. */
export function lastInitConfig(google: FakeGoogle): GoogleIdConfiguration {
    const call = google.initialize.mock.calls.at(-1);
    if (!call) throw new Error('Google was not initialized');
    return call[0];
}

interface RenderOptions {
    path?: string;
    signedIn?: boolean;
    googleClientId?: string;
    google?: FakeGoogle;
    loadGoogle?: () => Promise<GoogleIdentity>;
}

export function renderApp(options: RenderOptions = {}) {
    const google = options.google ?? fakeGoogle();
    const services = createAuthServices(
        { apiBaseUrl: TEST_API_BASE_URL, googleClientId: options.googleClientId ?? CLIENT_ID },
        options.loadGoogle ?? (() => Promise.resolve(google)),
    );
    if (options.signedIn) {
        services.store.signIn({
        access_token: TOKEN,
        expires_at: new Date(Date.now() + 8 * 3_600_000).toISOString(),
        user: USER,
        });
    }
    render(
        <MemoryRouter initialEntries={[options.path ?? '/']}>
        <AuthProvider services={services}>
            <App />
        </AuthProvider>
        </MemoryRouter>,
    );
    return { services, google };
}