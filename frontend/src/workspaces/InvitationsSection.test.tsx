import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import {
    errorBodyFixture,
    INVITATION_ID,
    invitationFixture,
    WORKSPACE_ID,
    workspaceFixture,
} from '../test/fixtures';
import { API, renderApp } from '../test/renderApp';
import { server } from '../test/server';

const wsUrl = `${API}/workspaces/${WORKSPACE_ID}`;
const invitationsUrl = `${wsUrl}/invitations`;
const open = () => renderApp({ signedIn: true, path: `/workspaces/${WORKSPACE_ID}` });
const list = () => screen.findByRole('list', { name: 'Invitations' });

const DARA_PENDING = invitationFixture();
const LISA_ACCEPTED = invitationFixture({
    id: '00000000-0000-4000-8000-000000000009',
    recipient: { id: '00000000-0000-4000-8000-000000000006', display_name: 'Lisa' },
    status: 'accepted',
});

beforeEach(() => {
    server.use(
        http.get(wsUrl, () => HttpResponse.json(workspaceFixture(), { headers: { ETag: '"2"' } })),
        http.get(`${wsUrl}/members`, () => HttpResponse.json({ items: [] })),
    );
});

describe('InvitationsSection list', () => {
    it('shows pending invitations by default, with Cancel only on pending ones', async () => {
        const statuses: (string | null)[] = [];
        server.use(
            http.get(invitationsUrl, async ({ request }) => {
                await delay(100);
                statuses.push(new URL(request.url).searchParams.get('status'));
                return HttpResponse.json({ items: [DARA_PENDING], next_cursor: null });
            }),
        );
        open();
        expect(await screen.findByText('Loading invitations…')).toBeInTheDocument();
        const rows = within(await list()).getAllByRole('listitem');
        expect(rows[0]).toHaveTextContent('Dara');
        expect(rows[0]).toHaveTextContent('Pending');
        expect(within(rows[0]!).getByRole('button', { name: 'Cancel invitation for Dara' })).toBeInTheDocument();
        expect(statuses).toEqual(['pending']);
    });

    it('is not shown to a member', async () => {
        server.use(
            http.get(wsUrl, () => HttpResponse.json(workspaceFixture({ my_role: 'member' }), { headers: { ETag: '"2"' } })),
        );
        open();
        expect(await screen.findByRole('heading', { name: 'DevOps Team' })).toBeInTheDocument();
        await screen.findByRole('heading', { name: 'Members' });
        expect(screen.queryByRole('heading', { name: 'Invitations' })).not.toBeInTheDocument();
    });

    it('shows an empty state', async () => {
        server.use(http.get(invitationsUrl, () => HttpResponse.json({ items: [], next_cursor: null })));
        open();
        expect(await screen.findByRole('heading', { name: 'No invitations' })).toBeInTheDocument();
        expect(screen.getByText(/no pending invitations/)).toBeInTheDocument();
    });

    it('requests the chosen status and offers no Cancel on finished invitations', async () => {
        const statuses: (string | null)[] = [];
        server.use(
            http.get(invitationsUrl, ({ request }) => {
                const status = new URL(request.url).searchParams.get('status');
                statuses.push(status);
                return HttpResponse.json({
                    items: status === 'accepted' ? [LISA_ACCEPTED] : [DARA_PENDING],
                    next_cursor: null,
                });
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.selectOptions(screen.getByLabelText('Show'), 'accepted');
        expect(await screen.findByText('Lisa')).toBeInTheDocument();
        const row = screen.getByText('Lisa').closest('li');
        expect(row).toHaveTextContent('Accepted');
        expect(within(row!).queryByRole('button')).not.toBeInTheDocument();

        await user.selectOptions(screen.getByLabelText('Show'), 'all');
        await waitFor(() => expect(statuses).toEqual(['pending', 'accepted', null]));
    });

    it('shows an error with a retry', async () => {
        let calls = 0;
        server.use(
            http.get(invitationsUrl, () => {
                calls += 1;
                if (calls === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), {
                        status: 503,
                        headers: { 'Retry-After': '2' },
                    });
                }
                return HttpResponse.json({ items: [DARA_PENDING], next_cursor: null });
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await list()).toBeInTheDocument();
    });

    it('shows the not-available page when the list says the workspace is gone', async () => {
        server.use(
            http.get(invitationsUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })),
        );
        open();
        expect(await screen.findByRole('heading', { name: 'Workspace not available' })).toBeInTheDocument();
    });

    it('explains that a full workspace cannot accept invitations', async () => {
        server.use(
            http.get(wsUrl, () => HttpResponse.json(workspaceFixture({ member_count: 20 }), { headers: { ETag: '"2"' } })),
            http.get(invitationsUrl, () => HttpResponse.json({ items: [], next_cursor: null })),
        );
        open();
        expect(await screen.findByText(/maximum of 20 members/)).toHaveTextContent('Invitations cannot be accepted');
        expect(screen.getByText(/do not reserve member slots/)).toBeInTheDocument();
    });
});

describe('InvitationsSection invite', () => {
    it('sends a trimmed email, shows a notice and reloads the list', async () => {
        let created = false;
        let seen: { key: string | null; body: unknown } | undefined;
        server.use(
            http.get(invitationsUrl, () =>
                HttpResponse.json({ items: created ? [DARA_PENDING] : [], next_cursor: null }),
            ),
            http.post(invitationsUrl, async ({ request }) => {
                seen = { key: request.headers.get('idempotency-key'), body: await request.json() };
                created = true;
                return HttpResponse.json(DARA_PENDING, { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No invitations' });

        await user.type(screen.getByLabelText('Email address'), '  dara@example.com  ');
        await user.click(screen.getByRole('button', { name: 'Send invitation' }));

        expect(await screen.findByText('Invitation sent to Dara.')).toBeInTheDocument();
        expect(await screen.findByRole('button', { name: 'Cancel invitation for Dara' })).toBeInTheDocument();
        expect(seen?.body).toEqual({ email: 'dara@example.com' });
        expect(seen?.key).toBeTruthy();
        expect(screen.getByLabelText('Email address')).toHaveValue('');
    });

    it.each(['', 'not-an-email'])('rejects %j without calling the server', async (raw) => {
        let posts = 0;
        server.use(
            http.get(invitationsUrl, () => HttpResponse.json({ items: [], next_cursor: null })),
            http.post(invitationsUrl, () => {
                posts += 1;
                return HttpResponse.json(DARA_PENDING, { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No invitations' });
        if (raw) await user.type(screen.getByLabelText('Email address'), raw);
        await user.click(screen.getByRole('button', { name: 'Send invitation' }));
        expect(screen.getByRole('alert')).toBeInTheDocument();
        expect(posts).toBe(0);
    });

    it.each([
        ['USER_NOT_FOUND', 404, 'No registered user'],
        ['EMAIL_AMBIGUOUS', 409, 'more than one account'],
        ['ALREADY_MEMBER', 409, 'already a member'],
        ['INVITATION_ALREADY_PENDING', 409, 'already has a pending invitation'],
        ['MEMBER_LIMIT_REACHED', 409, 'maximum of 20 members'],
    ])('explains %s', async (code, status, fragment) => {
        server.use(
            http.get(invitationsUrl, () => HttpResponse.json({ items: [], next_cursor: null })),
            http.post(invitationsUrl, () => HttpResponse.json(errorBodyFixture(code), { status })),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No invitations' });
        await user.type(screen.getByLabelText('Email address'), 'someone@example.com');
        await user.click(screen.getByRole('button', { name: 'Send invitation' }));
        expect(await screen.findByRole('alert')).toHaveTextContent(fragment);
        // The workspace page is still there: an unknown email is not a lost workspace.
        expect(screen.getByRole('heading', { name: 'DevOps Team' })).toBeInTheDocument();
    });

    it('shows the server reason for an invalid email', async () => {
        server.use(
            http.get(invitationsUrl, () => HttpResponse.json({ items: [], next_cursor: null })),
            http.post(invitationsUrl, () =>
                HttpResponse.json(errorBodyFixture('VALIDATION_FAILED', [{ field: 'email', reason: 'Email is not allowed.' }]), {
                    status: 422,
                }),
            ),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No invitations' });
        await user.type(screen.getByLabelText('Email address'), 'a@b.co');
        await user.click(screen.getByRole('button', { name: 'Send invitation' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Email is not allowed.');
    });

    it('retries after a temporary failure with the same Idempotency-Key', async () => {
        const keys: (string | null)[] = [];
        server.use(
            http.get(invitationsUrl, () => HttpResponse.json({ items: [], next_cursor: null })),
            http.post(invitationsUrl, ({ request }) => {
                keys.push(request.headers.get('idempotency-key'));
                if (keys.length === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), {
                        status: 503,
                        headers: { 'Retry-After': '1' },
                    });
                }
                return HttpResponse.json(DARA_PENDING, { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No invitations' });
        await user.type(screen.getByLabelText('Email address'), 'dara@example.com');
        await user.click(screen.getByRole('button', { name: 'Send invitation' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('will not send a duplicate');

        await user.click(screen.getByRole('button', { name: 'Send invitation' }));
        expect(await screen.findByText('Invitation sent to Dara.')).toBeInTheDocument();
        expect(keys).toHaveLength(2);
        expect(keys[1]).toBe(keys[0]);
    });
});

describe('InvitationsSection cancel', () => {
    it('asks for confirmation, can be kept, and returns focus to the trigger', async () => {
        let cancels = 0;
        server.use(
            http.get(invitationsUrl, () => HttpResponse.json({ items: [DARA_PENDING], next_cursor: null })),
            http.post(`${API}/invitations/${INVITATION_ID}/cancel`, () => {
                cancels += 1;
                return HttpResponse.json(invitationFixture({ status: 'cancelled' }));
            }),
        );
        const user = userEvent.setup();
        open();
        await user.click(await screen.findByRole('button', { name: 'Cancel invitation for Dara' }));
        expect(screen.getByText(/They will no longer be able to accept it/)).toBeInTheDocument();
        await waitFor(() => expect(screen.getByRole('button', { name: 'Keep invitation' })).toHaveFocus());

        await user.click(screen.getByRole('button', { name: 'Keep invitation' }));
        expect(screen.queryByRole('button', { name: 'Yes, cancel invitation' })).not.toBeInTheDocument();
        await waitFor(() =>
            expect(screen.getByRole('button', { name: 'Cancel invitation for Dara' })).toHaveFocus(),
        );
        expect(cancels).toBe(0);
    });

    it('cancels after confirmation and reloads the list', async () => {
        let cancelled = false;
        server.use(
            http.get(invitationsUrl, () =>
                HttpResponse.json({ items: cancelled ? [] : [DARA_PENDING], next_cursor: null }),
            ),
            http.post(`${API}/invitations/${INVITATION_ID}/cancel`, () => {
                cancelled = true;
                return HttpResponse.json(invitationFixture({ status: 'cancelled' }));
            }),
        );
        const user = userEvent.setup();
        open();
        await user.click(await screen.findByRole('button', { name: 'Cancel invitation for Dara' }));
        await user.click(screen.getByRole('button', { name: 'Yes, cancel invitation' }));
        expect(await screen.findByText('The invitation for Dara was cancelled.')).toBeInTheDocument();
        expect(await screen.findByRole('heading', { name: 'No invitations' })).toBeInTheDocument();
    });

    it('shows the real state when the invitation was already finished', async () => {
        let finished = false;
        server.use(
            http.get(invitationsUrl, () =>
                HttpResponse.json({
                    items: [finished ? invitationFixture({ status: 'accepted' }) : DARA_PENDING],
                    next_cursor: null,
                }),
            ),
            http.post(`${API}/invitations/${INVITATION_ID}/cancel`, () => {
                finished = true;
                return HttpResponse.json(errorBodyFixture('INVITATION_NOT_PENDING'), { status: 409 });
            }),
        );
        const user = userEvent.setup();
        open();
        await user.click(await screen.findByRole('button', { name: 'Cancel invitation for Dara' }));
        await user.click(screen.getByRole('button', { name: 'Yes, cancel invitation' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('already been accepted, declined or cancelled');
        await waitFor(() =>
            expect(within(screen.getByRole('list', { name: 'Invitations' })).getByText('Accepted')).toBeInTheDocument(),
        );
        expect(screen.queryByRole('button', { name: 'Cancel invitation for Dara' })).not.toBeInTheDocument();
    });
});