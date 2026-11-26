import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { errorBodyFixture, workspaceFixture } from '../test/fixtures';
import { API, meHandler, renderApp, USER } from '../test/renderApp';
import { server } from '../test/server';

const OWNED_ID = '00000000-0000-4000-8000-0000000000a1';
const JOINED_ID = '00000000-0000-4000-8000-0000000000a2';
const OWNED = workspaceFixture({ id: OWNED_ID, name: 'DevOps Team', my_role: 'owner', member_count: 2 });
const JOINED = workspaceFixture({ id: JOINED_ID, name: 'Design Club', my_role: 'member', member_count: 5 });

const listHandler = (items: unknown[], nextCursor: string | null = null) =>
    http.get(`${API}/workspaces`, () => HttpResponse.json({ items, next_cursor: nextCursor }));

const open = () => renderApp({ signedIn: true, path: '/workspaces' });

describe('WorkspacesPage list', () => {
    it('lists owned and joined workspaces with roles, sizes and links', async () => {
        server.use(meHandler(1), listHandler([OWNED, JOINED]));
        open();
        expect(screen.getByText('Loading workspaces…')).toBeInTheDocument();

        const ownedLink = await screen.findByRole('link', { name: 'DevOps Team' });
        expect(ownedLink).toHaveAttribute('href', `/workspaces/${OWNED_ID}`);
        const ownedRow = ownedLink.closest('li');
        expect(ownedRow).toHaveTextContent('Owner');
        expect(ownedRow).toHaveTextContent('2 of 20 members');

        const joinedRow = screen.getByRole('link', { name: 'Design Club' }).closest('li');
        expect(joinedRow).toHaveTextContent('Member');
        expect(joinedRow).toHaveTextContent('5 of 20 members');
    });

    it('explains that only owned workspaces count toward the limit of ten', async () => {
        server.use(meHandler(1), listHandler([OWNED, JOINED]));
        open();
        expect(await screen.findByText(/You own 1 of 10 workspaces/)).toHaveTextContent(
            'Workspaces you have joined do not count toward this limit.',
        );
        expect(screen.getByRole('button', { name: 'Create workspace' })).toBeEnabled();
    });

    it('shows an empty state when there are no workspaces', async () => {
        server.use(meHandler(0), listHandler([]));
        open();
        expect(await screen.findByRole('heading', { name: 'No workspaces yet' })).toBeInTheDocument();
    });

    it('shows an error with a retry, then the list', async () => {
        let calls = 0;
        server.use(
            meHandler(1),
            http.get(`${API}/workspaces`, () => {
                calls += 1;
                if (calls === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), {
                        status: 503,
                        headers: { 'Retry-After': '2' },
                    });
                }
                return HttpResponse.json({ items: [OWNED], next_cursor: null });
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByRole('link', { name: 'DevOps Team' })).toBeInTheDocument();
    });

    it('loads more pages with the cursor and keeps earlier items', async () => {
        const cursors: (string | null)[] = [];
        server.use(
            meHandler(1),
            http.get(`${API}/workspaces`, ({ request }) => {
                const cursor = new URL(request.url).searchParams.get('cursor');
                cursors.push(cursor);
                return cursor === null
                    ? HttpResponse.json({ items: [OWNED], next_cursor: 'opaque-1' })
                    : HttpResponse.json({ items: [JOINED], next_cursor: null });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('link', { name: 'DevOps Team' });
        await user.click(screen.getByRole('button', { name: 'Load more' }));
        expect(await screen.findByRole('link', { name: 'Design Club' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'DevOps Team' })).toBeInTheDocument();
        expect(cursors).toEqual([null, 'opaque-1']);
        expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
    });
});

describe('WorkspacesPage create', () => {
    it('creates a workspace with a trimmed name, then refreshes the list and the count', async () => {
        let ownedCount = 1;
        let created = false;
        let seen: { key: string | null; body: unknown } | undefined;
        server.use(
            http.get(`${API}/me`, () => HttpResponse.json({ user: USER, owned_workspace_count: ownedCount })),
            http.get(`${API}/workspaces`, () =>
                HttpResponse.json({
                    items: created ? [workspaceFixture({ id: OWNED_ID, name: 'New Team', version: 1, member_count: 1 })] : [],
                    next_cursor: null,
                }),
            ),
            http.post(`${API}/workspaces`, async ({ request }) => {
                seen = { key: request.headers.get('idempotency-key'), body: await request.json() };
                created = true;
                ownedCount = 2;
                return HttpResponse.json(
                    workspaceFixture({ id: OWNED_ID, name: 'New Team', version: 1, member_count: 1 }),
                    { status: 201, headers: { ETag: '"1"' } },
                );
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No workspaces yet' });

        await user.type(screen.getByLabelText('Workspace name'), '  New Team  ');
        await user.click(screen.getByRole('button', { name: 'Create workspace' }));

        expect(await screen.findByText(/New Team.* created/)).toBeInTheDocument();
        expect(await screen.findByRole('link', { name: 'New Team' })).toBeInTheDocument();
        expect(await screen.findByText(/You own 2 of 10 workspaces/)).toBeInTheDocument();
        expect(seen?.body).toEqual({ name: 'New Team' });
        expect(seen?.key).toBeTruthy();
        expect(screen.getByLabelText('Workspace name')).toHaveValue('');
    });

    it('rejects an empty name without calling the server', async () => {
        let posts = 0;
        server.use(
            meHandler(0),
            listHandler([]),
            http.post(`${API}/workspaces`, () => {
                posts += 1;
                return HttpResponse.json(workspaceFixture(), { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No workspaces yet' });
        await user.click(screen.getByRole('button', { name: 'Create workspace' }));
        expect(screen.getByRole('alert')).toHaveTextContent('Enter a workspace name.');
        expect(posts).toBe(0);
    });

    it('disables creation at ten owned workspaces and explains why', async () => {
        server.use(meHandler(10), listHandler([OWNED]));
        open();
        expect(await screen.findByText(/the maximum/)).toHaveTextContent('Workspaces you have joined do not count');
        expect(screen.getByRole('button', { name: 'Create workspace' })).toBeDisabled();
    });

    it('shows the same explanation when the server rejects the eleventh workspace', async () => {
        server.use(
            meHandler(3),
            listHandler([]),
            http.post(`${API}/workspaces`, () =>
                HttpResponse.json(errorBodyFixture('WORKSPACE_LIMIT_REACHED'), { status: 409 }),
            ),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No workspaces yet' });
        await user.type(screen.getByLabelText('Workspace name'), 'One too many');
        await user.click(screen.getByRole('button', { name: 'Create workspace' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('the maximum');
    });

    it('shows the server field error for an invalid name', async () => {
        server.use(
            meHandler(0),
            listHandler([]),
            http.post(`${API}/workspaces`, () =>
                HttpResponse.json(errorBodyFixture('VALIDATION_FAILED', [{ field: 'name', reason: 'Name is not allowed.' }]), {
                    status: 422,
                }),
            ),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No workspaces yet' });
        await user.type(screen.getByLabelText('Workspace name'), 'Bad');
        await user.click(screen.getByRole('button', { name: 'Create workspace' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Name is not allowed.');
    });

    it('retries after a temporary failure with the same Idempotency-Key', async () => {
        const keys: (string | null)[] = [];
        server.use(
            meHandler(0),
            listHandler([]),
            http.post(`${API}/workspaces`, ({ request }) => {
                keys.push(request.headers.get('idempotency-key'));
                if (keys.length === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), {
                        status: 503,
                        headers: { 'Retry-After': '1' },
                    });
                }
                return HttpResponse.json(workspaceFixture({ version: 1, member_count: 1 }), { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No workspaces yet' });
        await user.type(screen.getByLabelText('Workspace name'), 'Retry Team');
        await user.click(screen.getByRole('button', { name: 'Create workspace' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('will not create a duplicate');

        await user.click(screen.getByRole('button', { name: 'Create workspace' }));
        expect(await screen.findByText(/Retry Team|DevOps Team/)).toBeInTheDocument();
        expect(keys).toHaveLength(2);
        expect(keys[1]).toBe(keys[0]);
    });
});