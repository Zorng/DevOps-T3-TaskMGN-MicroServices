import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { errorBodyFixture, INVITATION_ID, invitationFixture, WORKSPACE_ID } from '../test/fixtures';
import { API, renderApp } from '../test/renderApp';
import { server } from '../test/server';

const url = `${API}/invitations/${INVITATION_ID}`;
const open = () => renderApp({ signedIn: true, path: `/invitations/${INVITATION_ID}` });

describe('InvitationPage', () => {
    it('shows loading, then a pending invitation with a way to respond', async () => {
        server.use(
            http.get(url, async () => {
                await delay(100);
                return HttpResponse.json(invitationFixture());
            }),
        );
        open();
        expect(await screen.findByText('Loading invitation…')).toBeInTheDocument();
        expect(await screen.findByRole('heading', { name: 'Invitation to DevOps Team' })).toBeInTheDocument();
        expect(screen.getByText('Invited by Example Owner')).toBeInTheDocument();
        expect(screen.getByText('This invitation is waiting for your answer.')).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Go to my invitations to accept or decline' })).toHaveAttribute(
            'href',
            '/invitations',
        );
    });

    it('links to the workspace once accepted', async () => {
        server.use(http.get(url, () => HttpResponse.json(invitationFixture({ status: 'accepted' }))));
        open();
        expect(await screen.findByRole('link', { name: 'Open workspace' })).toHaveAttribute('href', `/workspaces/${WORKSPACE_ID}`);
    });

    it('says a cancelled invitation was cancelled', async () => {
        server.use(http.get(url, () => HttpResponse.json(invitationFixture({ status: 'cancelled' }))));
        open();
        expect(await screen.findByText('This invitation was cancelled by the workspace owner.')).toBeInTheDocument();
    });

    it('shows not available for a deleted or hidden invitation', async () => {
        server.use(http.get(url, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })));
        open();
        expect(await screen.findByRole('heading', { name: 'Invitation not available' })).toBeInTheDocument();
    });

    it('shows an error with a retry', async () => {
        let calls = 0;
        server.use(
            http.get(url, () => {
                calls += 1;
                if (calls === 1) return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                return HttpResponse.json(invitationFixture());
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByRole('heading', { name: 'Invitation to DevOps Team' })).toBeInTheDocument();
    });
});
