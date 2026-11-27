import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import { BOARD_ID, boardFixture, errorBodyFixture, WORKSPACE_ID, workspaceFixture } from '../test/fixtures';
import { API, renderApp } from '../test/renderApp';
import { server } from '../test/server';

const wsUrl = `${API}/workspaces/${WORKSPACE_ID}`;
const boardUrl = `${wsUrl}/boards/${BOARD_ID}`;
const DONE_ID = '00000000-0000-4000-8000-000000000005';
const TODO = boardFixture();
const DONE = boardFixture({ id: DONE_ID, name: 'Done' });
const open = (id = BOARD_ID) => renderApp({ signedIn: true, path: `/workspaces/${WORKSPACE_ID}/boards/${id}` });

const workspaceHandler = (role: 'owner' | 'member' = 'owner') =>
    http.get(wsUrl, () => HttpResponse.json(workspaceFixture({ my_role: role }), { headers: { ETag: '"2"' } }));

// The page reads the workspace, the board and the board list. The workspace page is also
// needed after a deletion, so its members and invitations are mocked too.
beforeEach(() => {
    server.use(
        workspaceHandler(),
        http.get(`${wsUrl}/boards`, () => HttpResponse.json({ items: [TODO, DONE] })),
        http.get(`${wsUrl}/boards/:boardId`, ({ params }) => {
            const found = [TODO, DONE].find((item) => item.id === params['boardId']);
            if (!found) return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            return HttpResponse.json(found, { headers: { ETag: '"1"' } });
        }),
        http.get(`${wsUrl}/members`, () => HttpResponse.json({ items: [] })),
        http.get(`${wsUrl}/invitations`, () => HttpResponse.json({ items: [], next_cursor: null })),
    );
});

describe('BoardPage view and navigation', () => {
    it('shows the board, marks it in the board navigation and links back to its workspace', async () => {
        open();
        expect(screen.getByText('Loading board…')).toBeInTheDocument();
        expect(await screen.findByRole('heading', { name: 'Todo' })).toBeInTheDocument();
        const nav = screen.getByRole('navigation', { name: 'Boards in this workspace' });
        expect(nav).toHaveTextContent('Todo');
        expect(nav).toHaveTextContent('Done');
        expect(screen.getByRole('link', { name: 'Todo' })).toHaveAttribute('aria-current', 'page');
        expect(screen.getByRole('link', { name: 'Done' })).not.toHaveAttribute('aria-current');
        expect(screen.getByRole('link', { name: 'Back to DevOps Team' })).toHaveAttribute(
            'href',
            `/workspaces/${WORKSPACE_ID}`,
        );
    });

    it('switches boards inside the same workspace', async () => {
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });
        await user.click(screen.getByRole('link', { name: 'Done' }));
        expect(await screen.findByRole('heading', { name: 'Done' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Done' })).toHaveAttribute('aria-current', 'page');
        expect(screen.getByRole('link', { name: 'Todo' })).toHaveAttribute(
            'href',
            `/workspaces/${WORKSPACE_ID}/boards/${BOARD_ID}`,
        );
    });

    it('shows a not-available page for a deleted board and keeps the way back to the workspace', async () => {
        open('00000000-0000-4000-8000-000000000099');
        expect(await screen.findByRole('heading', { name: 'Board not available' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Back to the workspace' })).toHaveAttribute(
            'href',
            `/workspaces/${WORKSPACE_ID}`,
        );
    });

    it('shows a not-available page when the whole workspace is hidden', async () => {
        server.use(http.get(wsUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })));
        open();
        expect(await screen.findByRole('heading', { name: 'Workspace not available' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Back to workspaces' })).toHaveAttribute('href', '/workspaces');
    });

    it('shows a denied page when access is forbidden', async () => {
        server.use(http.get(boardUrl, () => HttpResponse.json(errorBodyFixture('FORBIDDEN'), { status: 403 })));
        open();
        expect(await screen.findByRole('heading', { name: 'Access denied' })).toBeInTheDocument();
    });

    it('shows an error with a retry', async () => {
        let calls = 0;
        server.use(
            http.get(`${wsUrl}/boards`, () => {
                calls += 1;
                if (calls === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                }
                return HttpResponse.json({ items: [TODO, DONE] });
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByRole('heading', { name: 'Todo' })).toBeInTheDocument();
    });
});

describe('BoardPage rename', () => {
    it('lets a member rename, sending If-Match, and updates the heading and the navigation', async () => {
        const seen: { ifMatch: string | null; body: unknown }[] = [];
        server.use(
            workspaceHandler('member'),
            http.patch(boardUrl, async ({ request }) => {
                seen.push({ ifMatch: request.headers.get('if-match'), body: await request.json() });
                return HttpResponse.json(boardFixture({ name: 'Backlog', version: 2 }), { headers: { ETag: '"2"' } });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });

        const input = screen.getByLabelText('Board name');
        await user.clear(input);
        await user.type(input, '  Backlog  ');
        await user.click(screen.getByRole('button', { name: 'Rename board' }));

        expect(await screen.findByRole('heading', { name: 'Backlog' })).toBeInTheDocument();
        expect(screen.getByText('Board renamed.')).toBeInTheDocument();
        expect(screen.getByRole('navigation', { name: 'Boards in this workspace' })).toHaveTextContent('Backlog');
        expect(seen).toEqual([{ ifMatch: '"1"', body: { name: 'Backlog' } }]);
    });

    it('rejects an empty or unchanged name without calling the server', async () => {
        let patches = 0;
        server.use(
            http.patch(boardUrl, () => {
                patches += 1;
                return HttpResponse.json(TODO);
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });

        const input = screen.getByLabelText('Board name');
        await user.clear(input);
        await user.click(screen.getByRole('button', { name: 'Rename board' }));
        expect(screen.getByRole('alert')).toHaveTextContent('Enter a board name.');

        await user.type(input, 'Todo');
        await user.click(screen.getByRole('button', { name: 'Rename board' }));
        expect(screen.getByRole('alert')).toHaveTextContent('Enter a different name');
        expect(patches).toBe(0);
    });

    it('after a stale version, shows the latest board, keeps the typed name and retries with the new version', async () => {
        const ifMatches: (string | null)[] = [];
        let reads = 0;
        server.use(
            http.get(boardUrl, () => {
                reads += 1;
                if (reads === 1) return HttpResponse.json(TODO, { headers: { ETag: '"1"' } });
                return HttpResponse.json(boardFixture({ name: 'Changed elsewhere', version: 2 }), {
                    headers: { ETag: '"2"' },
                });
            }),
            http.patch(boardUrl, ({ request }) => {
                ifMatches.push(request.headers.get('if-match'));
                if (ifMatches.length === 1) {
                    return HttpResponse.json(errorBodyFixture('VERSION_CONFLICT'), { status: 412 });
                }
                return HttpResponse.json(boardFixture({ name: 'Mine', version: 3 }), { headers: { ETag: '"3"' } });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });

        const input = screen.getByLabelText('Board name');
        await user.clear(input);
        await user.type(input, 'Mine');
        await user.click(screen.getByRole('button', { name: 'Rename board' }));

        expect(await screen.findByRole('alert')).toHaveTextContent('changed by someone else');
        expect(screen.getByRole('alert')).toHaveTextContent('latest version of the board is shown');
        expect(await screen.findByRole('heading', { name: 'Changed elsewhere' })).toBeInTheDocument();
        expect(screen.getByLabelText('Board name')).toHaveValue('Mine');

        await user.click(screen.getByRole('button', { name: 'Rename board' }));
        expect(await screen.findByRole('heading', { name: 'Mine' })).toBeInTheDocument();
        expect(ifMatches).toEqual(['"1"', '"2"']);
    });

    it('trusts the server when a rename is refused even though the form was enabled', async () => {
        server.use(
            workspaceHandler('member'),
            http.patch(boardUrl, () => HttpResponse.json(errorBodyFixture('FORBIDDEN'), { status: 403 })),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });
        const input = screen.getByLabelText('Board name');
        await user.clear(input);
        await user.type(input, 'Nope');
        await user.click(screen.getByRole('button', { name: 'Rename board' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('permission');
    });

    it('shows the unavailable page when the board was deleted before the rename', async () => {
        let deleted = false;
        server.use(
            http.get(boardUrl, () =>
                deleted
                    ? HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })
                    : HttpResponse.json(TODO, { headers: { ETag: '"1"' } }),
            ),
            http.patch(boardUrl, () => {
                deleted = true;
                return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });
        const input = screen.getByLabelText('Board name');
        await user.clear(input);
        await user.type(input, 'Other');
        await user.click(screen.getByRole('button', { name: 'Rename board' }));
        expect(await screen.findByRole('heading', { name: 'Board not available' })).toBeInTheDocument();
    });
});

describe('BoardPage delete', () => {
    it('offers no delete to a member', async () => {
        server.use(workspaceHandler('member'));
        open();
        await screen.findByRole('heading', { name: 'Todo' });
        expect(screen.queryByRole('button', { name: 'Delete board' })).not.toBeInTheDocument();
        expect(screen.getByText('Only the workspace owner can delete a board.')).toBeInTheDocument();
    });

    it('asks for confirmation that names the tasks and comments, and can be cancelled', async () => {
        let deletes = 0;
        server.use(
            http.delete(boardUrl, () => {
                deletes += 1;
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });

        await user.click(screen.getByRole('button', { name: 'Delete board' }));
        expect(screen.getByRole('heading', { name: 'Delete “Todo”?' })).toBeInTheDocument();
        expect(screen.getByText(/every task and comment in it/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();

        await user.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.getByRole('button', { name: 'Delete board' })).toHaveFocus();
        expect(deletes).toBe(0);
    });

    it('deletes with If-Match and returns to the workspace with a notice', async () => {
        let ifMatch: string | null = null;
        server.use(
            http.delete(boardUrl, ({ request }) => {
                ifMatch = request.headers.get('if-match');
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });
        await user.click(screen.getByRole('button', { name: 'Delete board' }));
        await user.click(screen.getByRole('button', { name: 'Yes, delete board' }));

        expect(await screen.findByRole('heading', { name: 'DevOps Team' })).toBeInTheDocument();
        expect(await screen.findByText('Board “Todo” deleted.')).toBeInTheDocument();
        expect(ifMatch).toBe('"1"');
    });

    it('after a stale version, shows the latest board and needs a new confirmation with the new version', async () => {
        const ifMatches: (string | null)[] = [];
        let reads = 0;
        server.use(
            http.get(boardUrl, () => {
                reads += 1;
                return HttpResponse.json(boardFixture({ version: reads === 1 ? 1 : 2 }), {
                    headers: { ETag: reads === 1 ? '"1"' : '"2"' },
                });
            }),
            http.delete(boardUrl, ({ request }) => {
                ifMatches.push(request.headers.get('if-match'));
                if (ifMatches.length === 1) {
                    return HttpResponse.json(errorBodyFixture('VERSION_CONFLICT'), { status: 412 });
                }
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });
        await user.click(screen.getByRole('button', { name: 'Delete board' }));
        await user.click(screen.getByRole('button', { name: 'Yes, delete board' }));

        expect(await screen.findByRole('alert')).toHaveTextContent('Confirm again');
        await waitFor(() => expect(reads).toBe(2));

        await user.click(screen.getByRole('button', { name: 'Yes, delete board' }));
        expect(await screen.findByText('Board “Todo” deleted.')).toBeInTheDocument();
        expect(ifMatches).toEqual(['"1"', '"2"']);
    });

    it('trusts the server when the owner lost permission: the delete control disappears after the refusal', async () => {
        let role: 'owner' | 'member' = 'owner';
        server.use(
            http.get(wsUrl, () => HttpResponse.json(workspaceFixture({ my_role: role }), { headers: { ETag: '"2"' } })),
            http.delete(boardUrl, () => {
                role = 'member';
                return HttpResponse.json(errorBodyFixture('FORBIDDEN'), { status: 403 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });
        await user.click(screen.getByRole('button', { name: 'Delete board' }));
        await user.click(screen.getByRole('button', { name: 'Yes, delete board' }));

        expect(await screen.findByText('Only the workspace owner can delete a board.')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Yes, delete board' })).not.toBeInTheDocument();
    });

    it('shows the unavailable page when the board was already deleted by someone else', async () => {
        let deleted = false;
        server.use(
            http.get(boardUrl, () =>
                deleted
                    ? HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })
                    : HttpResponse.json(TODO, { headers: { ETag: '"1"' } }),
            ),
            http.delete(boardUrl, () => {
                deleted = true;
                return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'Todo' });
        await user.click(screen.getByRole('button', { name: 'Delete board' }));
        await user.click(screen.getByRole('button', { name: 'Yes, delete board' }));
        expect(await screen.findByRole('heading', { name: 'Board not available' })).toBeInTheDocument();
    });
});
