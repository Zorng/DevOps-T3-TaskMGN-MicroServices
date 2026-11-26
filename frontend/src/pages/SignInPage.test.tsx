import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { server } from '../test/server';
import {
    API,
    CLIENT_ID,
    challenge,
    challengeHandler,
    errorBody,
    lastInitConfig,
    meHandler,
    renderApp,
    sessionBody,
} from '../test/renderApp';

describe('SignInPage', () => {
    it('prepares Google with the client ID and the server nonce', async () => {
        server.use(challengeHandler());
        const { google } = renderApp({ path: '/sign-in' });

        expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveTextContent('Preparing Google sign-in');

        await waitFor(() => expect(google.renderButton).toHaveBeenCalledTimes(1));
        const config = lastInitConfig(google);
        expect(config.client_id).toBe(CLIENT_ID);
        expect(config.nonce).toBe(challenge(1).nonce);
        expect(config.auto_select).toBe(false);
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
    });

    it('exchanges the Google credential and returns to the page the visitor wanted', async () => {
        let exchanged: unknown;
        server.use(
            challengeHandler(),
            meHandler(),
            http.get(`${API}/workspaces`, () => HttpResponse.json({ items: [], next_cursor: null })),
            http.post(`${API}/auth/google/exchange`, async ({ request }) => {
                exchanged = await request.json();
                return HttpResponse.json(sessionBody());
            }),
        );
        const { google, services } = renderApp({ path: '/workspaces' }); // signed out: redirected to /sign-in

        await waitFor(() => expect(google.initialize).toHaveBeenCalledTimes(1));
        await act(async () => {
            lastInitConfig(google).callback({ credential: 'google-id-token' });
        });

        expect(await screen.findByRole('heading', { name: 'Workspaces' })).toBeInTheDocument();
        expect(exchanged).toEqual({ challenge_id: challenge(1).challenge_id, id_token: 'google-id-token' });
        expect(services.store.getSnapshot().status).toBe('signed_in');
        expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
    });

    it('shows a friendly error and starts over with a fresh challenge on retry', async () => {
        let challenges = 0;
        server.use(
            http.post(`${API}/auth/google/challenges`, () => {
                challenges += 1;
                return HttpResponse.json(challenge(challenges), { status: 201 });
            }),
            http.post(`${API}/auth/google/exchange`, () =>
                HttpResponse.json(errorBody('AUTH_CHALLENGE_INVALID'), { status: 401 }),
            ),
        );
        const user = userEvent.setup();
        const { google, services } = renderApp({ path: '/sign-in' });

        await waitFor(() => expect(google.initialize).toHaveBeenCalledTimes(1));
        await act(async () => {
            lastInitConfig(google).callback({ credential: 'google-id-token' });
        });
        expect(await screen.findByRole('alert')).toHaveTextContent('sign-in attempt expired');
        expect(services.store.getSnapshot().status).toBe('signed_out');

        await user.click(screen.getByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(google.initialize).toHaveBeenCalledTimes(2));
        expect(lastInitConfig(google).nonce).toBe(challenge(2).nonce);
    });

    it('explains when Google returned no credential', async () => {
        server.use(challengeHandler());
        const { google } = renderApp({ path: '/sign-in' });
        await waitFor(() => expect(google.initialize).toHaveBeenCalledTimes(1));
        await act(async () => {
            lastInitConfig(google).callback({});
        });
        expect(await screen.findByRole('alert')).toHaveTextContent('did not return');
    });

    it('explains when Google sign-in is not configured and offers no retry', async () => {
        const { google } = renderApp({ path: '/sign-in', googleClientId: '' });
        expect(await screen.findByRole('alert')).toHaveTextContent('not configured');
        expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
        expect(google.initialize).not.toHaveBeenCalled();
    });

    it('reports when the Google script cannot be loaded', async () => {
        server.use(challengeHandler());
        renderApp({ path: '/sign-in', loadGoogle: () => Promise.reject(new Error('blocked')) });
        expect(await screen.findByRole('alert')).toHaveTextContent('could not be loaded');
        expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    });

    it('sends an already signed-in user away from the sign-in page', async () => {
        server.use(meHandler());
        renderApp({ path: '/sign-in', signedIn: true });
        expect(await screen.findByRole('heading', { name: 'Task Management' })).toBeInTheDocument();
    });
});