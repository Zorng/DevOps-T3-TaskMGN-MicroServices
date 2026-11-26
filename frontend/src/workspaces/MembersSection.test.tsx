import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it, beforeEach } from 'vitest';
import {
    errorBodyFixture,
    memberFixture,
    MEMBER_ID,
    OWNER_ID,
    WORKSPACE_ID,
    workspaceFixture,
} from '../test/fixtures';
import { API, meHandler, renderApp } from '../test/renderApp';
import { server } from '../test/server';

const wsUrl = `${API}/workspaces/${WORKSPACE_ID}`;
const membersUrl = `${wsUrl}/members`;

const OWNER_ROW = memberFixture({
    membership_id: '00000000-0000-4000-8000-000000000011',
    user: { id: OWNER_ID, display_name: 'Example Owner' },
    role: 'owner',
});
const DARA_ROW = memberFixture();
const ME_AS_MEMBER = memberFixture({
    membership_id: '00000000-0000-4000-8000-000000000012',
    user: { id: OWNER_ID, display_name: 'Example Owner' },
    role: 'member',
});
const SOMEONE_ELSE_OWNER = memberFixture({
    membership_id: '00000000-0000-4000-8000-000000000013',
    user: { id: '00000000-0000-4000-8000-000000000005', display_name: 'Team Lead' },
    role: 'owner',
});

const open = () => renderApp({ signedIn: true, path: `/workspaces/${WORKSPACE_ID}` });
const workspaceHandler = (overrides: Record<string, unknown> = {}, etag = '"2"') =>
    http.get(wsUrl, () => HttpResponse.json(workspaceFixture(overrides), { headers: { ETag: etag } }));
const membersHandler = (items: unknown[]) => http.get(membersUrl, () => HttpResponse.json({ items }));
const memberList = () => screen.findByRole('list', { name: 'Workspace members' });
beforeEach(() => {
    server.use(http.get(`${wsUrl}/invitations`, () => HttpResponse.json({ items: [], next_cursor: null })));
});

describe('MembersSection view', () => {
    it('lists members with roles and gives the owner Remove only for non-owners', async () => {
        server.use(workspaceHandler(), membersHandler([OWNER_ROW, DARA_ROW]));
        open();
        const rows = within(await memberList()).getAllByRole('listitem');
        expect(rows).toHaveLength(2);
        expect(rows[0]).toHaveTextContent('Example Owner (you)');
        expect(rows[0]).toHaveTextContent('Owner');
        expect(within(rows[0]!).queryByRole('button')).not.toBeInTheDocument();
        expect(rows[1]).toHaveTextContent('Dara');
        expect(rows[1]).toHaveTextContent('Member');
        expect(within(rows[1]!).getByRole('button', { name: 'Remove Dara' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Leave workspace' })).not.toBeInTheDocument();
        expect(screen.getByText(/you cannot leave this workspace/)).toBeInTheDocument();
    });

    it('gives a member Leave and no Remove buttons', async () => {
        server.use(workspaceHandler({ my_role: 'member' }), membersHandler([SOMEONE_ELSE_OWNER, ME_AS_MEMBER]));
        open();
        await memberList();
        expect(screen.getByRole('button', { name: 'Leave workspace' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Remove/ })).not.toBeInTheDocument();
    });

    it('explains a full workspace', async () => {
        const twenty = Array.from({ length: 20 }, (_, i) =>
            memberFixture({
                membership_id: `00000000-0000-4000-8000-0000000001${String(i).padStart(2, '0')}`,
                user: { id: `00000000-0000-4000-8000-0000000002${String(i).padStart(2, '0')}`, display_name: `Person ${i + 1}` },
                role: i === 0 ? 'owner' : 'member',
            }),
        );
        server.use(workspaceHandler({ member_count: 20 }), membersHandler(twenty));
        open();
        await memberList();
        const section = screen.getByRole('region', { name: 'Members' });
        expect(within(section).getByText(/maximum of 20 members/)).toHaveTextContent('do not reserve slots');
    });

    it('shows a loading state, then an error with a retry', async () => {
        let calls = 0;
        server.use(
            workspaceHandler(),
            http.get(membersUrl, () => {
                calls += 1;
                if (calls === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), {
                        status: 503,
                        headers: { 'Retry-After': '2' },
                    });
                }
                return HttpResponse.json({ items: [OWNER_ROW] });
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByText('Loading members…')).toBeInTheDocument();
        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await memberList()).toBeInTheDocument();
    });

    it('shows the not-available page when the member list says the workspace is gone', async () => {
        server.use(
            workspaceHandler(),
            http.get(membersUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })),
        );
        open();
        expect(await screen.findByRole('heading', { name: 'Workspace not available' })).toBeInTheDocument();
    });
});

describe('MembersSection remove', () => {
    it('confirms, removes, reloads the list and re-reads the workspace', async () => {
        let members = [OWNER_ROW, DARA_ROW];
        let workspaceReads = 0;
        let deletedPath: string | null = null;
        server.use(
            http.get(wsUrl, () => {
                workspaceReads += 1;
                return workspaceReads === 1
                    ? HttpResponse.json(workspaceFixture(), { headers: { ETag: '"2"' } })
                    : HttpResponse.json(workspaceFixture({ version: 3, member_count: 1 }), { headers: { ETag: '"3"' } });
            }),
            http.get(membersUrl, () => HttpResponse.json({ items: members })),
            http.delete(`${membersUrl}/${MEMBER_ID}`, ({ request }) => {
                deletedPath = new URL(request.url).pathname;
                members = [OWNER_ROW];
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();

        await user.click(await screen.findByRole('button', { name: 'Remove Dara' }));
        expect(screen.getByText(/Remove Dara from this workspace\?/)).toBeInTheDocument();
        expect(screen.getByText(/task assignments here are cleared/)).toBeInTheDocument();
        await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus());

        await user.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByRole('button', { name: 'Yes, remove Dara' })).not.toBeInTheDocument();
        await waitFor(() => expect(screen.getByRole('button', { name: 'Remove Dara' })).toHaveFocus());
        expect(deletedPath).toBeNull();

        await user.click(screen.getByRole('button', { name: 'Remove Dara' }));
        await user.click(screen.getByRole('button', { name: 'Yes, remove Dara' }));

        expect(await screen.findByText('Dara was removed from the workspace.')).toBeInTheDocument();
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove Dara' })).not.toBeInTheDocument());
        expect(deletedPath).toBe(`/api/v1/workspaces/${WORKSPACE_ID}/members/${MEMBER_ID}`);
        await waitFor(() => expect(screen.getByText(/Your role/)).toHaveTextContent('1 of 20 members'));
    });

    it('reloads the list when the member was already gone', async () => {
        let members = [OWNER_ROW, DARA_ROW];
        server.use(
            workspaceHandler(),
            http.get(membersUrl, () => HttpResponse.json({ items: members })),
            http.delete(`${membersUrl}/${MEMBER_ID}`, () => {
                members = [OWNER_ROW];
                return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            }),
        );
        const user = userEvent.setup();
        open();
        await user.click(await screen.findByRole('button', { name: 'Remove Dara' }));
        await user.click(screen.getByRole('button', { name: 'Yes, remove Dara' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('no longer in this workspace');
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove Dara' })).not.toBeInTheDocument());
    });

    it('drops the owner controls when the server says the role has changed', async () => {
        let workspaceReads = 0;
        server.use(
            http.get(wsUrl, () => {
                workspaceReads += 1;
                return HttpResponse.json(workspaceFixture(workspaceReads === 1 ? {} : { my_role: 'member', version: 3 }), {
                    headers: { ETag: workspaceReads === 1 ? '"2"' : '"3"' },
                });
            }),
            membersHandler([SOMEONE_ELSE_OWNER, ME_AS_MEMBER, DARA_ROW]),
            http.delete(`${membersUrl}/${MEMBER_ID}`, () =>
                HttpResponse.json(errorBodyFixture('FORBIDDEN'), { status: 403 }),
            ),
        );
        const user = userEvent.setup();
        // The first read says "owner", so the screen starts with owner controls.
        open();
        await user.click(await screen.findByRole('button', { name: 'Remove Dara' }));
        await user.click(screen.getByRole('button', { name: 'Yes, remove Dara' }));

        expect(await screen.findByRole('alert')).toHaveTextContent('permission');
        expect(screen.getByRole('alert')).toHaveTextContent('latest information');
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove Dara' })).not.toBeInTheDocument());
        expect(screen.getByText('Only the workspace owner can rename or delete it.')).toBeInTheDocument();
    });
});

describe('MembersSection leave', () => {
    it('confirms, leaves, and returns to the list with a notice', async () => {
        let left = false;
        server.use(
            workspaceHandler({ my_role: 'member' }),
            membersHandler([SOMEONE_ELSE_OWNER, ME_AS_MEMBER]),
            meHandler(0),
            http.get(`${API}/workspaces`, () => HttpResponse.json({ items: [], next_cursor: null })),
            http.delete(`${wsUrl}/membership`, () => {
                left = true;
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();
        await user.click(await screen.findByRole('button', { name: 'Leave workspace' }));
        expect(screen.getByText(/You will lose access to its boards and tasks/)).toBeInTheDocument();
        await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus());

        await user.click(screen.getByRole('button', { name: 'Yes, leave workspace' }));
        expect(await screen.findByText(/You left .*DevOps Team/)).toBeInTheDocument();
        expect(screen.getByRole('heading', { name: 'Workspaces' })).toBeInTheDocument();
        expect(left).toBe(true);
    });

    it('can be cancelled without calling the server', async () => {
        let deletes = 0;
        server.use(
            workspaceHandler({ my_role: 'member' }),
            membersHandler([SOMEONE_ELSE_OWNER, ME_AS_MEMBER]),
            http.delete(`${wsUrl}/membership`, () => {
                deletes += 1;
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();
        await user.click(await screen.findByRole('button', { name: 'Leave workspace' }));
        await user.click(screen.getByRole('button', { name: 'Cancel' }));
        await waitFor(() => expect(screen.getByRole('button', { name: 'Leave workspace' })).toHaveFocus());
        expect(deletes).toBe(0);
    });

    it('shows the not-available page when access was already revoked', async () => {
        let workspaceReads = 0;
        server.use(
            http.get(wsUrl, () => {
                workspaceReads += 1;
                return workspaceReads === 1
                    ? HttpResponse.json(workspaceFixture({ my_role: 'member' }), { headers: { ETag: '"2"' } })
                    : HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            }),
            membersHandler([SOMEONE_ELSE_OWNER, ME_AS_MEMBER]),
            http.delete(`${wsUrl}/membership`, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })),
        );
        const user = userEvent.setup();
        open();
        await user.click(await screen.findByRole('button', { name: 'Leave workspace' }));
        await user.click(screen.getByRole('button', { name: 'Yes, leave workspace' }));
        expect(await screen.findByRole('heading', { name: 'Workspace not available' })).toBeInTheDocument();
    });
});