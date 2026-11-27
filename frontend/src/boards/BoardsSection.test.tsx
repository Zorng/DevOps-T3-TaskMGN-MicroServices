import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import { BOARD_ID, boardFixture, errorBodyFixture, WORKSPACE_ID, workspaceFixture } from '../test/fixtures';
import { API, renderApp } from '../test/renderApp';
import { server } from '../test/server';

const wsUrl = `${API}/workspaces/${WORKSPACE_ID}`;
const boardsUrl = `${wsUrl}/boards`;
const open = () => renderApp({ signedIn: true, path: `/workspaces/${WORKSPACE_ID}` });
const list = () => screen.findByRole('list', { name: 'Boards' });

const board = (n: number, name: string) =>
    boardFixture({ id: `00000000-0000-4000-8000-0000000001${n}0`, name });
const FIVE = [board(1, 'Backlog'), board(2, 'Todo'), board(3, 'Doing'), board(4, 'Review'), board(5, 'Done')];

const workspaceHandler = (role: 'owner' | 'member' = 'owner') =>
    http.get(wsUrl, () => HttpResponse.json(workspaceFixture({ my_role: role }), { headers: { ETag: '"2"' } }));

beforeEach(() => {
    server.use(
        workspaceHandler(),
        http.get(`${wsUrl}/members`, () => HttpResponse.json({ items: [] })),
        http.get(`${wsUrl}/invitations`, () => HttpResponse.json({ items: [], next_cursor: null })),
    );
});

describe('BoardsSection list', () => {
    it('shows a loading state, then the boards in server order, each linking to its board', async () => {
        server.use(
            http.get(boardsUrl, async () => {
                await delay(100);
                return HttpResponse.json({ items: [boardFixture(), board(2, 'Done')] });
            }),
        );
        open();
        expect(await screen.findByText('Loading boards…')).toBeInTheDocument();
        const links = within(await list()).getAllByRole('link');
        expect(links.map((link) => link.textContent)).toEqual(['Todo', 'Done']);
        expect(links[0]).toHaveAttribute('href', `/workspaces/${WORKSPACE_ID}/boards/${BOARD_ID}`);
        expect(screen.getByText('2 of 5 boards')).toBeInTheDocument();
    });

    it('shows an empty workspace as empty and does not create any board itself', async () => {
        let posts = 0;
        server.use(
            http.get(boardsUrl, () => HttpResponse.json({ items: [] })),
            http.post(boardsUrl, () => {
                posts += 1;
                return HttpResponse.json(boardFixture(), { status: 201 });
            }),
        );
        open();
        expect(await screen.findByRole('heading', { name: 'No boards yet' })).toBeInTheDocument();
        expect(screen.getByText('0 of 5 boards')).toBeInTheDocument();
        expect(screen.queryByRole('list', { name: 'Boards' })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Create board' })).toBeEnabled();
        expect(posts).toBe(0);
    });

    it('shows the five-board limit and stops offering creation', async () => {
        server.use(http.get(boardsUrl, () => HttpResponse.json({ items: FIVE })));
        open();
        expect(within(await list()).getAllByRole('listitem')).toHaveLength(5);
        expect(screen.getByText('5 of 5 boards')).toBeInTheDocument();
        expect(screen.getByText(/has the maximum of 5 boards/)).toBeInTheDocument();
        expect(screen.getByLabelText('Board name')).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Create board' })).toBeDisabled();
    });

    it('lets a member create boards too', async () => {
        server.use(workspaceHandler('member'), http.get(boardsUrl, () => HttpResponse.json({ items: [] })));
        open();
        expect(await screen.findByRole('heading', { name: 'No boards yet' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Create board' })).toBeEnabled();
    });

    it('shows an error with a retry when the boards cannot be loaded', async () => {
        let calls = 0;
        server.use(
            http.get(boardsUrl, () => {
                calls += 1;
                if (calls === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                }
                return HttpResponse.json({ items: [boardFixture()] });
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(within(await list()).getByRole('link', { name: 'Todo' })).toBeInTheDocument();
    });
});

describe('BoardsSection create', () => {
    it('creates a board with a trimmed name and an Idempotency-Key, then reloads the list', async () => {
        const seen: { key: string | null; body: unknown }[] = [];
        let created = false;
        server.use(
            http.get(boardsUrl, () => HttpResponse.json({ items: created ? [boardFixture()] : [] })),
            http.post(boardsUrl, async ({ request }) => {
                seen.push({ key: request.headers.get('idempotency-key'), body: await request.json() });
                created = true;
                return HttpResponse.json(boardFixture(), { status: 201, headers: { ETag: '"1"' } });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No boards yet' });

        await user.type(screen.getByLabelText('Board name'), '  Todo  ');
        await user.click(screen.getByRole('button', { name: 'Create board' }));

        expect(await screen.findByText('Board “Todo” created.')).toBeInTheDocument();
        expect(within(await list()).getByRole('link', { name: 'Todo' })).toBeInTheDocument();
        expect(screen.getByLabelText('Board name')).toHaveValue('');
        expect(seen).toHaveLength(1);
        expect(seen[0]?.body).toEqual({ name: 'Todo' });
        expect(seen[0]?.key).toBeTruthy();
    });

    it('rejects an empty or too long name without calling the server', async () => {
        let posts = 0;
        server.use(
            http.get(boardsUrl, () => HttpResponse.json({ items: [] })),
            http.post(boardsUrl, () => {
                posts += 1;
                return HttpResponse.json(boardFixture(), { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No boards yet' });

        await user.click(screen.getByRole('button', { name: 'Create board' }));
        expect(screen.getByRole('alert')).toHaveTextContent('Enter a board name.');

        fireEvent.change(screen.getByLabelText('Board name'), { target: { value: 'x'.repeat(51) } });
        await user.click(screen.getByRole('button', { name: 'Create board' }));
        expect(screen.getByRole('alert')).toHaveTextContent('50 characters or fewer');
        expect(posts).toBe(0);
    });

    it('shows the server’s reason for a rejected name', async () => {
        server.use(
            http.get(boardsUrl, () => HttpResponse.json({ items: [] })),
            http.post(boardsUrl, () =>
                HttpResponse.json(errorBodyFixture('VALIDATION_FAILED', [{ field: 'name', reason: 'Name is not allowed.' }]), {
                    status: 422,
                }),
            ),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No boards yet' });
        await user.type(screen.getByLabelText('Board name'), 'Odd');
        await user.click(screen.getByRole('button', { name: 'Create board' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Name is not allowed.');
        expect(screen.getByLabelText('Board name')).toHaveValue('Odd');
    });

    it('trusts the server over the screen when the limit is reached by someone else', async () => {
        let full = false;
        server.use(
            http.get(boardsUrl, () => HttpResponse.json({ items: full ? FIVE : FIVE.slice(0, 4) })),
            http.post(boardsUrl, () => {
                full = true; // another member created the fifth board first
                return HttpResponse.json(errorBodyFixture('BOARD_LIMIT_REACHED'), { status: 409 });
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        expect(screen.getByText('4 of 5 boards')).toBeInTheDocument();

        await user.type(screen.getByLabelText('Board name'), 'Sixth');
        await user.click(screen.getByRole('button', { name: 'Create board' }));

        expect(await screen.findByRole('alert')).toHaveTextContent('maximum of 5 boards');
        expect(await screen.findByText('5 of 5 boards')).toBeInTheDocument();
        expect(screen.getByLabelText('Board name')).toBeDisabled();
    });

    it('retries after an unclear failure with the same Idempotency-Key', async () => {
        const keys: (string | null)[] = [];
        server.use(
            http.get(boardsUrl, () => HttpResponse.json({ items: [] })),
            http.post(boardsUrl, ({ request }) => {
                keys.push(request.headers.get('idempotency-key'));
                if (keys.length === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                }
                return HttpResponse.json(boardFixture(), { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No boards yet' });

        await user.type(screen.getByLabelText('Board name'), 'Todo');
        await user.click(screen.getByRole('button', { name: 'Create board' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('will not create a duplicate');

        await user.click(screen.getByRole('button', { name: 'Create board' }));
        await waitFor(() => expect(keys).toHaveLength(2));
        expect(keys[0]).toBe(keys[1]);
        expect(await screen.findByText('Board “Todo” created.')).toBeInTheDocument();
    });

    it('re-reads the workspace when creation is refused because access was lost', async () => {
        let workspaceReads = 0;
        server.use(
            http.get(wsUrl, () => {
                workspaceReads += 1;
                if (workspaceReads === 1) {
                    return HttpResponse.json(workspaceFixture(), { headers: { ETag: '"2"' } });
                }
                return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            }),
            http.get(boardsUrl, () => HttpResponse.json({ items: [] })),
            http.post(boardsUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No boards yet' });
        await user.type(screen.getByLabelText('Board name'), 'Todo');
        await user.click(screen.getByRole('button', { name: 'Create board' }));
        expect(await screen.findByRole('heading', { name: 'Workspace not available' })).toBeInTheDocument();
    });
});
