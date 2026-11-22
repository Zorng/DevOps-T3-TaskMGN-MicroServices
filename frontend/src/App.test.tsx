import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { App } from './App';

function renderAt(path: string, googleClientId = 'id') {
    render(
        <MemoryRouter initialEntries={[path]}>
            <App config={{ apiBaseUrl: 'http://localhost:8080', googleClientId }} />
        </MemoryRouter>,
    );
}

describe('App', () => {
    it('renders the home page', () => {
        renderAt('/');
        expect(screen.getByRole('heading', { name: 'Task Management' })).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('warns when Google sign-in is not configured', () => {
        renderAt('/', '');
        expect(screen.getByRole('alert')).toHaveTextContent('VITE_GOOGLE_CLIENT_ID');
    });

    it('navigates with the main navigation', async () => {
        const user = userEvent.setup();
        renderAt('/');
        await user.click(screen.getByRole('link', { name: 'Workspaces' }));
        expect(screen.getByRole('heading', { name: 'Workspaces' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Workspaces' })).toHaveAttribute('aria-current', 'page');
    });

    it('shows a not-found page for unknown routes', () => {
        renderAt('/nope');
        expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    });

    it('makes the skip link the first keyboard stop', async () => {
        const user = userEvent.setup();
        renderAt('/');
        await user.tab();
        expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveFocus();
    });
});