import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { server } from './test/server';
import { API, TOKEN, challengeHandler, errorBody, meHandler, renderApp } from './test/renderApp';

describe('access control and routing', () => {
    it('sends a signed-out visitor to the sign-in page and hides the main navigation', async () => {
        server.use(challengeHandler());
        const { google } = renderApp({ path: '/workspaces' });

        expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
        expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Sign in' })).toBeInTheDocument();
        await waitFor(() => expect(google.renderButton).toHaveBeenCalled());
    });

    it('shows the main navigation to a signed-in user and marks the current page', async () => {
        server.use(meHandler());
        const user = userEvent.setup();
        renderApp({ signedIn: true });

        await user.click(screen.getByRole('link', { name: 'Workspaces' }));
        expect(await screen.findByRole('heading', { name: 'Workspaces' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Workspaces' })).toHaveAttribute('aria-current', 'page');
    });

    it('shows a not-found page for unknown routes', () => {
        renderApp({ path: '/nope' });
        expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    });

    it('makes the skip link the first keyboard stop', async () => {
        server.use(meHandler());
        const user = userEvent.setup();
        renderApp({ signedIn: true });
        await user.tab();
        expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveFocus();
    });
});

describe('home page profile', () => {
    it('shows the signed-in user and the owned workspace count', async () => {
        server.use(meHandler(4));
        renderApp({ signedIn: true });

        expect(screen.getByRole('status')).toHaveTextContent('Loading your profile');
        expect(await screen.findByText('You own 4 of 10 workspaces.')).toBeInTheDocument();
        expect(screen.getByText('Signed in as Example Owner.')).toBeInTheDocument();
    });

    it('lets the user retry when loading the profile fails', async () => {
        let calls = 0;
        server.use(
            http.get(`${API}/me`, () => {
                calls += 1;
                if (calls === 1) {
                    return HttpResponse.json(errorBody('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                }
                return HttpResponse.json({
                    user: { id: 'u', display_name: 'Example Owner', email: 'o@example.com', created_at: '2026-11-14T10:00:00Z' },
                    owned_workspace_count: 2,
                });
            }),
        );
        const user = userEvent.setup();
        renderApp({ signedIn: true });

        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByText('You own 2 of 10 workspaces.')).toBeInTheDocument();
    });

    it('signs the user out and returns to sign-in when the gateway answers 401', async () => {
        server.use(
            http.get(`${API}/me`, () => HttpResponse.json(errorBody('SESSION_INVALID'), { status: 401 })),
            challengeHandler(),
        );
        const { services, google } = renderApp({ signedIn: true });

        expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
        expect(services.store.getSnapshot().status).toBe('signed_out');
        await waitFor(() => expect(google.renderButton).toHaveBeenCalled());
    });
});

describe('signing out', () => {
    it('revokes the session, clears local state, and tells Google not to auto-select', async () => {
        let authorization: string | null = null;
        server.use(
            meHandler(),
            challengeHandler(),
            http.post(`${API}/auth/logout`, ({ request }) => {
                authorization = request.headers.get('authorization');
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        const { services, google } = renderApp({ signedIn: true });

        await user.click(screen.getByRole('button', { name: 'Sign out' }));

        expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
        expect(authorization).toBe(`Bearer ${TOKEN}`);
        expect(services.store.getToken()).toBeNull();
        await waitFor(() => expect(google.disableAutoSelect).toHaveBeenCalled());
        await waitFor(() => expect(google.renderButton).toHaveBeenCalled());
    });

    it('still signs out locally and says so when the server cannot confirm (503)', async () => {
        server.use(
            meHandler(),
            challengeHandler(),
            http.post(`${API}/auth/logout`, () =>
                HttpResponse.json(errorBody('DEPENDENCY_UNAVAILABLE'), { status: 503 }),
            ),
        );
        const user = userEvent.setup();
        const { services, google } = renderApp({ signedIn: true });

        await user.click(screen.getByRole('button', { name: 'Sign out' }));

        expect(await screen.findByText(/server could not confirm/)).toBeInTheDocument();
        expect(services.store.getSnapshot().status).toBe('signed_out');
        await waitFor(() => expect(google.renderButton).toHaveBeenCalled());
    });
});