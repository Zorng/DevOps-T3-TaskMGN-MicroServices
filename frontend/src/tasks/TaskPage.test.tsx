import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import {
    BOARD_ID,
    boardFixture,
    errorBodyFixture,
    memberFixture,
    MEMBER_ID,
    OWNER_ID,
    TASK_ID,
    taskFixture,
    WORKSPACE_ID,
    workspaceFixture,
} from '../test/fixtures';
import { API, renderApp } from '../test/renderApp';
import { server } from '../test/server';

const wsUrl = `${API}/workspaces/${WORKSPACE_ID}`;
const taskUrl = `${wsUrl}/tasks/${TASK_ID}`;
const DONE_ID = '00000000-0000-4000-8000-000000000005';
const TODO = boardFixture();
const DONE = boardFixture({ id: DONE_ID, name: 'Done' });
const OWNER_ROW = memberFixture({
    membership_id: '00000000-0000-4000-8000-000000000011',
    user: { id: OWNER_ID, display_name: 'Example Owner' },
    role: 'owner',
});
const DARA_ROW = memberFixture();
const open = () => renderApp({ signedIn: true, path: `/workspaces/${WORKSPACE_ID}/tasks/${TASK_ID}` });
const editForm = () => screen.getByRole('form', { name: 'Edit task' });

const taskHandler = (overrides: Record<string, unknown> = {}, etag = '"1"') =>
    http.get(taskUrl, () => HttpResponse.json(taskFixture(overrides), { headers: { ETag: etag } }));

let memberReads = 0;

beforeEach(() => {
    memberReads = 0;
    server.use(
        http.get(wsUrl, () => HttpResponse.json(workspaceFixture({ my_role: 'member' }), { headers: { ETag: '"2"' } })),
        taskHandler(),
        http.get(`${wsUrl}/boards`, () => HttpResponse.json({ items: [TODO, DONE] })),
        http.get(`${wsUrl}/boards/:boardId`, ({ params }) => {
            const found = [TODO, DONE].find((item) => item.id === params['boardId']);
            if (!found) return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            return HttpResponse.json(found, { headers: { ETag: '"1"' } });
        }),
        http.get(`${wsUrl}/members`, () => {
            memberReads += 1;
            return HttpResponse.json({ items: [OWNER_ROW, DARA_ROW] });
        }),
        http.get(`${wsUrl}/tasks`, () => HttpResponse.json({ items: [], next_cursor: null })),
    );
});

describe('TaskPage view', () => {
    it('shows the task, its read-only server fields, and a link back to its board', async () => {
        open();
        expect(screen.getByText('Loading task…')).toBeInTheDocument();
        expect(await screen.findByRole('heading', { name: '#1 Build task service' })).toBeInTheDocument();
        const details = screen.getByLabelText('Task details');
        expect(details).toHaveTextContent('Todo');
        expect(details).toHaveTextContent('Dara');
        expect(details).toHaveTextContent('2026-11-15');
        expect(details).toHaveTextContent('2026-11-20');
        expect(details).toHaveTextContent('Example Owner');
        expect(details).toHaveTextContent('2026-11-14T10:00:00Z');
        expect(screen.getAllByText('Implement the agreed contract.').length).toBeGreaterThan(0);
        expect(screen.getByRole('link', { name: 'Back to Todo' })).toHaveAttribute(
            'href',
            `/workspaces/${WORKSPACE_ID}/boards/${BOARD_ID}`,
        );
    });

    it('has only the fixed fields: no priority, status or custom fields', async () => {
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        const labels = within(editForm())
            .getAllByRole('textbox')
            .concat(within(editForm()).getAllByRole('combobox'))
            .map((element) => element.id);
        expect(labels.sort()).toEqual(['edit-task-assignee', 'edit-task-description', 'edit-task-title']);
        expect(screen.queryByLabelText(/priority|status/i)).not.toBeInTheDocument();
    });

    it('shows a not-available page for a deleted task', async () => {
        server.use(http.get(taskUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })));
        open();
        expect(await screen.findByRole('heading', { name: 'Task not available' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Back to the workspace' })).toHaveAttribute(
            'href',
            `/workspaces/${WORKSPACE_ID}`,
        );
    });

    it('shows the workspace as unavailable when it is hidden', async () => {
        server.use(http.get(wsUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })));
        open();
        expect(await screen.findByRole('heading', { name: 'Workspace not available' })).toBeInTheDocument();
    });

    it('shows a denied page when access is forbidden', async () => {
        server.use(http.get(taskUrl, () => HttpResponse.json(errorBodyFixture('FORBIDDEN'), { status: 403 })));
        open();
        expect(await screen.findByRole('heading', { name: 'Access denied' })).toBeInTheDocument();
    });

    it('shows an error with a retry', async () => {
        let calls = 0;
        server.use(
            http.get(taskUrl, () => {
                calls += 1;
                if (calls === 1) return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                return HttpResponse.json(taskFixture(), { headers: { ETag: '"1"' } });
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByRole('heading', { name: '#1 Build task service' })).toBeInTheDocument();
    });

    it('shows an assignee who is no longer a member instead of silently clearing them', async () => {
        server.use(taskHandler({ assignee: { id: 'ghost', display_name: 'Ghost' } }));
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        const select = within(editForm()).getByLabelText('Assignee');
        await waitFor(() => expect(select).toHaveValue('ghost'));
        expect(within(select).getByRole('option', { name: 'Ghost (no longer a member)' })).toBeInTheDocument();
    });
});

describe('TaskPage edit', () => {
    it('sends only the changed field with If-Match, and never re-sends an unchanged assignee', async () => {
        const seen: { ifMatch: string | null; body: unknown }[] = [];
        server.use(
            http.patch(taskUrl, async ({ request }) => {
                seen.push({ ifMatch: request.headers.get('if-match'), body: await request.json() });
                return HttpResponse.json(taskFixture({ title: 'New title', version: 2 }), { headers: { ETag: '"2"' } });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        const title = within(editForm()).getByLabelText('Title');
        await user.clear(title);
        await user.type(title, '  New title  ');
        await user.click(within(editForm()).getByRole('button', { name: 'Save task' }));

        expect(await screen.findByRole('heading', { name: '#1 New title' })).toBeInTheDocument();
        expect(screen.getByText('Task saved.')).toBeInTheDocument();
        expect(seen).toEqual([{ ifMatch: '"1"', body: { title: 'New title' } }]);
    });

    it('does not call the server when nothing changed', async () => {
        let patches = 0;
        server.use(
            http.patch(taskUrl, () => {
                patches += 1;
                return HttpResponse.json(taskFixture());
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        await user.click(within(editForm()).getByRole('button', { name: 'Save task' }));
        expect(screen.getByRole('alert')).toHaveTextContent('There are no changes to save.');
        expect(patches).toBe(0);
    });

    it('assigns a member to an unassigned task', async () => {
        let body: unknown;
        server.use(
            taskHandler({ assignee: null }),
            http.patch(taskUrl, async ({ request }) => {
                body = await request.json();
                return HttpResponse.json(taskFixture({ version: 2 }), { headers: { ETag: '"2"' } });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        const select = within(editForm()).getByLabelText('Assignee');
        await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3));
        await user.selectOptions(select, 'Dara');
        await user.click(within(editForm()).getByRole('button', { name: 'Save task' }));
        expect(await screen.findByText('Task saved.')).toBeInTheDocument();
        expect(body).toEqual({ assignee_id: MEMBER_ID });
    });

    it('clears the assignee and a date with null, leaving everything else out', async () => {
        let body: unknown;
        server.use(
            http.patch(taskUrl, async ({ request }) => {
                body = await request.json();
                return HttpResponse.json(taskFixture({ assignee: null, due_date: null, version: 2 }), {
                    headers: { ETag: '"2"' },
                });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        const select = within(editForm()).getByLabelText('Assignee');
        await waitFor(() => expect(select).toHaveValue(MEMBER_ID));
        await user.selectOptions(select, 'Unassigned');
        fireEvent.change(within(editForm()).getByLabelText('Due date'), { target: { value: '' } });
        await user.click(within(editForm()).getByRole('button', { name: 'Save task' }));
        expect(await screen.findByText('Task saved.')).toBeInTheDocument();
        expect(body).toEqual({ assignee_id: null, due_date: null });
        expect(screen.getByLabelText('Task details')).toHaveTextContent('Unassigned');
    });

    it('checks the date order before sending', async () => {
        let patches = 0;
        server.use(
            http.patch(taskUrl, () => {
                patches += 1;
                return HttpResponse.json(taskFixture());
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        fireEvent.change(within(editForm()).getByLabelText('Due date'), { target: { value: '2026-11-01' } });
        await user.click(within(editForm()).getByRole('button', { name: 'Save task' }));
        expect(screen.getByText(/on or after the start date/)).toBeInTheDocument();
        expect(patches).toBe(0);
    });

    it('shows the server’s reason for a revoked assignee and re-reads the members', async () => {
        server.use(
            taskHandler({ assignee: null }),
            http.patch(taskUrl, () =>
                HttpResponse.json(
                    errorBodyFixture('VALIDATION_FAILED', [{ field: 'assignee_id', reason: 'That user is not a member.' }]),
                    { status: 422 },
                ),
            ),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        const select = within(editForm()).getByLabelText('Assignee');
        await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3));
        await user.selectOptions(select, 'Dara');
        const before = memberReads;
        await user.click(within(editForm()).getByRole('button', { name: 'Save task' }));
        expect(await screen.findByText('That user is not a member.')).toBeInTheDocument();
        await waitFor(() => expect(memberReads).toBeGreaterThan(before));
    });

    it('after a stale version shows the latest task, keeps the typed text, and retries with the new version', async () => {
        const ifMatches: (string | null)[] = [];
        let reads = 0;
        server.use(
            http.get(taskUrl, () => {
                reads += 1;
                if (reads === 1) return HttpResponse.json(taskFixture(), { headers: { ETag: '"1"' } });
                return HttpResponse.json(taskFixture({ title: 'Changed elsewhere', version: 2 }), {
                    headers: { ETag: '"2"' },
                });
            }),
            http.patch(taskUrl, ({ request }) => {
                ifMatches.push(request.headers.get('if-match'));
                if (ifMatches.length === 1) return HttpResponse.json(errorBodyFixture('VERSION_CONFLICT'), { status: 412 });
                return HttpResponse.json(taskFixture({ title: 'Mine', version: 3 }), { headers: { ETag: '"3"' } });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        const title = within(editForm()).getByLabelText('Title');
        await user.clear(title);
        await user.type(title, 'Mine');
        await user.click(within(editForm()).getByRole('button', { name: 'Save task' }));

        expect(await screen.findByText(/changed by someone else/)).toBeInTheDocument();
        expect(await screen.findByRole('heading', { name: '#1 Changed elsewhere' })).toBeInTheDocument();
        expect(within(editForm()).getByLabelText('Title')).toHaveValue('Mine');

        await user.click(within(editForm()).getByRole('button', { name: 'Save task' }));
        expect(await screen.findByRole('heading', { name: '#1 Mine' })).toBeInTheDocument();
        expect(ifMatches).toEqual(['"1"', '"2"']);
    });

    it('shows the workspace as unavailable when access was revoked before saving', async () => {
        let revoked = false;
        server.use(
            http.get(wsUrl, () =>
                revoked
                    ? HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })
                    : HttpResponse.json(workspaceFixture({ my_role: 'member' }), { headers: { ETag: '"2"' } }),
            ),
            http.patch(taskUrl, () => {
                revoked = true;
                return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        const title = within(editForm()).getByLabelText('Title');
        await user.clear(title);
        await user.type(title, 'Other');
        await user.click(within(editForm()).getByRole('button', { name: 'Save task' }));
        expect(await screen.findByRole('heading', { name: 'Workspace not available' })).toBeInTheDocument();
    });
});

describe('TaskPage move', () => {
    it('moves to another board of the workspace, keeping the task and its details', async () => {
        let seen: { ifMatch: string | null; body: unknown } | undefined;
        server.use(
            http.patch(taskUrl, async ({ request }) => {
                seen = { ifMatch: request.headers.get('if-match'), body: await request.json() };
                return HttpResponse.json(taskFixture({ board_id: DONE_ID, version: 2 }), { headers: { ETag: '"2"' } });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        const select = screen.getByLabelText('Destination board');
        expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual(['Choose a board…', 'Done']);
        expect(screen.getByRole('button', { name: 'Move task' })).toBeDisabled();

        await user.selectOptions(select, 'Done');
        await user.click(screen.getByRole('button', { name: 'Move task' }));

        expect(await screen.findByText('Task moved to Done.')).toBeInTheDocument();
        expect(screen.getByRole('heading', { name: '#1 Build task service' })).toBeInTheDocument();
        expect(screen.getByLabelText('Task details')).toHaveTextContent('Dara');
        expect(screen.getByRole('link', { name: 'Back to Done' })).toBeInTheDocument();
        expect(seen).toEqual({ ifMatch: '"1"', body: { board_id: DONE_ID } });
    });

    it('shows the server’s reason when the destination board was deleted', async () => {
        server.use(
            http.patch(taskUrl, () =>
                HttpResponse.json(
                    errorBodyFixture('VALIDATION_FAILED', [{ field: 'board_id', reason: 'That board no longer exists.' }]),
                    { status: 422 },
                ),
            ),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        await user.selectOptions(screen.getByLabelText('Destination board'), 'Done');
        await user.click(screen.getByRole('button', { name: 'Move task' }));
        expect(await screen.findByText('That board no longer exists.')).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Back to Todo' })).toBeInTheDocument();
    });

    it('offers no move when the workspace has no other board', async () => {
        server.use(http.get(`${wsUrl}/boards`, () => HttpResponse.json({ items: [TODO] })));
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        expect(screen.getByLabelText('Destination board')).toBeDisabled();
        expect(screen.getByText(/no other board/)).toBeInTheDocument();
    });
});

describe('TaskPage delete', () => {
    it('asks for confirmation that mentions the comments, and can be cancelled', async () => {
        let deletes = 0;
        server.use(
            http.delete(taskUrl, () => {
                deletes += 1;
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        await user.click(screen.getByRole('button', { name: 'Delete task' }));
        expect(screen.getByRole('heading', { name: 'Delete task #1?' })).toBeInTheDocument();
        expect(screen.getByText(/all of its comments/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();

        await user.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.getByRole('button', { name: 'Delete task' })).toHaveFocus();
        expect(deletes).toBe(0);
    });

    it('deletes with If-Match and returns to the board with a notice', async () => {
        let ifMatch: string | null = null;
        server.use(
            http.delete(taskUrl, ({ request }) => {
                ifMatch = request.headers.get('if-match');
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        await user.click(screen.getByRole('button', { name: 'Delete task' }));
        await user.click(screen.getByRole('button', { name: 'Yes, delete task' }));

        expect(await screen.findByRole('heading', { name: 'Todo' })).toBeInTheDocument();
        expect(await screen.findByText('Task #1 deleted.')).toBeInTheDocument();
        expect(ifMatch).toBe('"1"');
    });

    it('after a stale version, shows the latest task and needs a new confirmation with the new version', async () => {
        const ifMatches: (string | null)[] = [];
        let reads = 0;
        server.use(
            http.get(taskUrl, () => {
                reads += 1;
                return HttpResponse.json(taskFixture({ version: reads === 1 ? 1 : 2 }), {
                    headers: { ETag: reads === 1 ? '"1"' : '"2"' },
                });
            }),
            http.delete(taskUrl, ({ request }) => {
                ifMatches.push(request.headers.get('if-match'));
                if (ifMatches.length === 1) return HttpResponse.json(errorBodyFixture('VERSION_CONFLICT'), { status: 412 });
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        await user.click(screen.getByRole('button', { name: 'Delete task' }));
        await user.click(screen.getByRole('button', { name: 'Yes, delete task' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Confirm again');
        await waitFor(() => expect(reads).toBe(2));

        await user.click(screen.getByRole('button', { name: 'Yes, delete task' }));
        expect(await screen.findByText('Task #1 deleted.')).toBeInTheDocument();
        expect(ifMatches).toEqual(['"1"', '"2"']);
    });

    it('shows the task as unavailable when someone else already deleted it', async () => {
        let deleted = false;
        server.use(
            http.get(taskUrl, () =>
                deleted
                    ? HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })
                    : HttpResponse.json(taskFixture(), { headers: { ETag: '"1"' } }),
            ),
            http.delete(taskUrl, () => {
                deleted = true;
                return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: '#1 Build task service' });
        await user.click(screen.getByRole('button', { name: 'Delete task' }));
        await user.click(screen.getByRole('button', { name: 'Yes, delete task' }));
        expect(await screen.findByRole('heading', { name: 'Task not available' })).toBeInTheDocument();
    });
});
