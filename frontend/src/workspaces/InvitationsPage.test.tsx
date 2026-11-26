import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { errorBodyFixture, INVITATION_ID, invitationFixture, WORKSPACE_ID } from '../test/fixtures';
import { API, renderApp } from '../test/renderApp';
import { server } from '../test/server';

const listUrl = `${API}/invitations`;
const responseUrl = `${API}/invitations/${INVITATION_ID}/response`;
const open = () => renderApp({ signedIn: true, path: '/invitations' });
const list = () => screen.findByRole('list', { name: 'My invitations' });

const PENDING = invitationFixture();
const ACCEPTED = invitationFixture({
    id: '00000000-0000-4000-8000-000000000009',
    workspace_name: 'Design Team',
    status: 'accepted',
});

/** A list that answers like the server: by status, and re-reading what a test changed. */
function serveList(items: () => unknown[]) {
    const statuses: (string | null)[] = [];
    server.use(
        http.get(listUrl, ({ request }) => {
            statuses.push(new URL(request.url).searchParams.get('status'));
            return HttpResponse.json({ items: items(), next_cursor: null });
        }),
    );
    return statuses;
}

describe('MyInvitationsPage list', () => {
    it('shows a loading state, then pending invitations with Accept and Decline', async () => {
        server.use(
            http.get(listUrl, async () => {
                await delay(100);
                return HttpResponse.json({ items: [PENDING], next_cursor: null });
            }),
        );
        open();
        expect(await screen.findByText('Loading invitations…')).toBeInTheDocument();
        const row = within(await list()).getByRole('listitem');
        expect(row).toHaveTextContent('DevOps Team');
        expect(row).toHaveTextContent('Invited by Example Owner');
        expect(row).toHaveTextContent('Pending');
        expect(within(row).getByRole('button', { name: 'Accept invitation to DevOps Team' })).toBeInTheDocument();
        expect(within(row).getByRole('button', { name: 'Decline invitation to DevOps Team' })).toBeInTheDocument();
    });

    it('requests pending invitations by default', async () => {
        const statuses = serveList(() => [PENDING]);
        open();
        await list();
        expect(statuses).toEqual(['pending']);
    });

    it('shows an empty state', async () => {
        serveList(() => []);
        open();
        expect(await screen.findByRole('heading', { name: 'No invitations' })).toBeInTheDocument();
        expect(screen.getByText('You have no pending invitations.')).toBeInTheDocument();
    });

    it('shows a service error and recovers on retry', async () => {
        let calls = 0;
        server.use(
            http.get(listUrl, () => {
                calls += 1;
                if (calls === 1) return HttpResponse.json(errorBodyFixture('INTERNAL_ERROR'), { status: 500 });
                return HttpResponse.json({ items: [PENDING], next_cursor: null });
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByRole('alert')).toHaveTextContent('The service ran into a problem');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByText('DevOps Team')).toBeInTheDocument();
    });

    it('requests the chosen status and offers no actions on finished invitations', async () => {
        const statuses = serveList(() => [ACCEPTED]);
        const user = userEvent.setup();
        open();
        await list();
        await user.selectOptions(screen.getByLabelText('Show'), 'accepted');
        await waitFor(() => expect(statuses).toContain('accepted'));
        const row = (await screen.findByText('Design Team')).closest('li');
        expect(row).toHaveTextContent('Accepted');
        expect(within(row!).queryByRole('button')).not.toBeInTheDocument();
    });

    it('sends no status when All is chosen', async () => {
        const statuses = serveList(() => [PENDING, ACCEPTED]);
        const user = userEvent.setup();
        open();
        await list();
        await user.selectOptions(screen.getByLabelText('Show'), 'all');
        await waitFor(() => expect(statuses).toContain(null));
        expect(await screen.findByText('Design Team')).toBeInTheDocument();
    });

    it('loads more pages with the cursor', async () => {
        const cursors: (string | null)[] = [];
        server.use(
            http.get(listUrl, ({ request }) => {
                const cursor = new URL(request.url).searchParams.get('cursor');
                cursors.push(cursor);
                return cursor
                    ? HttpResponse.json({ items: [ACCEPTED], next_cursor: null })
                    : HttpResponse.json({ items: [PENDING], next_cursor: 'page-2' });
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.click(await screen.findByRole('button', { name: 'Load more' }));
        expect(await screen.findByText('Design Team')).toBeInTheDocument();
        expect(cursors).toEqual([null, 'page-2']);
        expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
    });

    it('has a navigation link that is current on this page', async () => {
        serveList(() => []);
        open();
        expect(await screen.findByRole('link', { name: 'Invitations' })).toHaveAttribute('aria-current', 'page');
    });
});

describe('MyInvitationsPage accept', () => {
    it('accepts, confirms, offers the workspace and refreshes the list', async () => {
        let accepted = false;
        let body: unknown;
        serveList(() => (accepted ? [] : [PENDING]));
        server.use(
            http.post(responseUrl, async ({ request }) => {
                body = await request.json();
                accepted = true;
                return HttpResponse.json(invitationFixture({ status: 'accepted' }));
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.click(screen.getByRole('button', { name: 'Accept invitation to DevOps Team' }));
        expect(await screen.findByRole('status')).toHaveTextContent('You joined “DevOps Team”.');
        expect(body).toEqual({ action: 'accept' });
        expect(screen.getByRole('link', { name: 'Open workspace' })).toHaveAttribute(
            'href',
            `/workspaces/${WORKSPACE_ID}`,
        );
        expect(await screen.findByRole('heading', { name: 'No invitations' })).toBeInTheDocument();
    });

    it('disables every action while a response is in flight, and sends it once', async () => {
        let requests = 0;
        serveList(() => [PENDING]);
        server.use(
            http.post(responseUrl, async () => {
                requests += 1;
                await delay(150);
                return HttpResponse.json(invitationFixture({ status: 'accepted' }));
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        const accept = screen.getByRole('button', { name: 'Accept invitation to DevOps Team' });
        await user.click(accept);
        expect(accept).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Decline invitation to DevOps Team' })).toBeDisabled();
        await screen.findByRole('status');
        expect(requests).toBe(1);
    });

    it('explains a full workspace and leaves the invitation pending and retryable', async () => {
        let calls = 0;
        serveList(() => [PENDING]);
        server.use(
            http.post(responseUrl, () => {
                calls += 1;
                if (calls === 1) return HttpResponse.json(errorBodyFixture('MEMBER_LIMIT_REACHED'), { status: 409 });
                return HttpResponse.json(invitationFixture({ status: 'accepted' }));
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.click(screen.getByRole('button', { name: 'Accept invitation to DevOps Team' }));
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent('maximum of 20 members');
        expect(alert).toHaveTextContent('still pending');
        const accept = screen.getByRole('button', { name: 'Accept invitation to DevOps Team' });
        expect(accept).toBeEnabled();
        await user.click(accept);
        expect(await screen.findByRole('status')).toHaveTextContent('You joined');
    });

    it('reloads when the invitation already reached another final state', async () => {
        let changed = false;
        serveList(() => (changed ? [] : [PENDING]));
        server.use(
            http.post(responseUrl, () => {
                changed = true;
                return HttpResponse.json(errorBodyFixture('INVITATION_NOT_PENDING'), { status: 409 });
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.click(screen.getByRole('button', { name: 'Accept invitation to DevOps Team' }));
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent('already accepted, declined or cancelled');
        expect(alert).toHaveTextContent('The latest list is shown.');
        expect(await screen.findByRole('heading', { name: 'No invitations' })).toBeInTheDocument();
    });

    it('handles a deleted workspace without exposing anything about it', async () => {
        let gone = false;
        serveList(() => (gone ? [] : [PENDING]));
        server.use(
            http.post(responseUrl, () => {
                gone = true;
                return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.click(screen.getByRole('button', { name: 'Accept invitation to DevOps Team' }));
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent('no longer available');
        expect(alert).toHaveTextContent('may have been deleted');
        expect(await screen.findByRole('heading', { name: 'No invitations' })).toBeInTheDocument();
    });

    it('says it is safe to try again after an unclear failure, and shows the real state', async () => {
        let loads = 0;
        server.use(
            http.get(listUrl, () => {
                loads += 1;
                return HttpResponse.json({ items: [PENDING], next_cursor: null });
            }),
            http.post(responseUrl, () =>
                HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 }),
            ),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.click(screen.getByRole('button', { name: 'Accept invitation to DevOps Team' }));
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent('temporarily unavailable');
        expect(alert).toHaveTextContent('It is safe to try again.');
        await waitFor(() => expect(loads).toBe(2));
        expect(screen.getByRole('button', { name: 'Accept invitation to DevOps Team' })).toBeEnabled();
    });

    it('shows a permission error as given by the code', async () => {
        serveList(() => [PENDING]);
        server.use(http.post(responseUrl, () => HttpResponse.json(errorBodyFixture('FORBIDDEN'), { status: 403 })));
        const user = userEvent.setup();
        open();
        await list();
        await user.click(screen.getByRole('button', { name: 'Accept invitation to DevOps Team' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('You do not have permission');
    });
});

describe('MyInvitationsPage decline', () => {
    it('asks for confirmation and sends nothing when the person keeps the invitation', async () => {
        let requests = 0;
        serveList(() => [PENDING]);
        server.use(
            http.post(responseUrl, () => {
                requests += 1;
                return HttpResponse.json(invitationFixture({ status: 'declined' }));
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        const decline = screen.getByRole('button', { name: 'Decline invitation to DevOps Team' });
        await user.click(decline);
        const group = screen.getByRole('group', { name: 'Confirm declining the invitation to DevOps Team' });
        expect(group).toHaveTextContent('You will need a new invitation to join later.');
        await user.click(within(group).getByRole('button', { name: 'Keep invitation' }));
        expect(requests).toBe(0);
        expect(screen.queryByRole('group')).not.toBeInTheDocument();
        await waitFor(() =>
            expect(screen.getByRole('button', { name: 'Decline invitation to DevOps Team' })).toHaveFocus(),
        );
    });

    it('declines after confirmation and refreshes the list', async () => {
        let declined = false;
        let body: unknown;
        serveList(() => (declined ? [] : [PENDING]));
        server.use(
            http.post(responseUrl, async ({ request }) => {
                body = await request.json();
                declined = true;
                return HttpResponse.json(invitationFixture({ status: 'declined' }));
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.click(screen.getByRole('button', { name: 'Decline invitation to DevOps Team' }));
        await user.click(screen.getByRole('button', { name: 'Yes, decline invitation' }));
        expect(await screen.findByRole('status')).toHaveTextContent('You declined the invitation to “DevOps Team”.');
        expect(body).toEqual({ action: 'decline' });
        expect(screen.queryByRole('link', { name: 'Open workspace' })).not.toBeInTheDocument();
        expect(await screen.findByRole('heading', { name: 'No invitations' })).toBeInTheDocument();
    });

    it('closes the confirmation and reports a failed decline', async () => {
        serveList(() => [PENDING]);
        server.use(
            http.post(responseUrl, () => HttpResponse.json(errorBodyFixture('INTERNAL_ERROR'), { status: 500 })),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.click(screen.getByRole('button', { name: 'Decline invitation to DevOps Team' }));
        await user.click(screen.getByRole('button', { name: 'Yes, decline invitation' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('It is safe to try again.');
        expect(screen.queryByRole('group')).not.toBeInTheDocument();
    });
});
