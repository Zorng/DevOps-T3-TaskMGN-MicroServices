import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
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
const tasksUrl = `${wsUrl}/tasks`;
const DONE_ID = '00000000-0000-4000-8000-000000000005';
const TODO = boardFixture();
const DONE = boardFixture({ id: DONE_ID, name: 'Done' });
const OWNER_ROW = memberFixture({
    membership_id: '00000000-0000-4000-8000-000000000011',
    user: { id: OWNER_ID, display_name: 'Example Owner' },
    role: 'owner',
});
const DARA_ROW = memberFixture();
const open = () => renderApp({ signedIn: true, path: `/workspaces/${WORKSPACE_ID}/boards/${BOARD_ID}` });
const taskList = () => screen.findByRole('list', { name: 'Tasks' });
const form = () => screen.getByRole('form', { name: 'Create a task' });

let boardReads = 0;
let memberReads = 0;

beforeEach(() => {
    boardReads = 0;
    memberReads = 0;
    server.use(
        http.get(wsUrl, () => HttpResponse.json(workspaceFixture(), { headers: { ETag: '"2"' } })),
        http.get(`${wsUrl}/boards`, () => {
            boardReads += 1;
            return HttpResponse.json({ items: [TODO, DONE] });
        }),
        http.get(`${wsUrl}/boards/:boardId`, ({ params }) => {
            const found = [TODO, DONE].find((item) => item.id === params['boardId']);
            if (!found) return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            return HttpResponse.json(found, { headers: { ETag: '"1"' } });
        }),
        http.get(`${wsUrl}/members`, () => {
            memberReads += 1;
            return HttpResponse.json({ items: [OWNER_ROW, DARA_ROW] });
        }),
        http.get(tasksUrl, () => HttpResponse.json({ items: [], next_cursor: null })),
    );
});

describe('TasksSection list', () => {
    it('shows loading, then the board’s tasks (filtered by board) with assignee, due date and a link', async () => {
        const boardIds: (string | null)[] = [];
        server.use(
            http.get(tasksUrl, async ({ request }) => {
                await delay(100);
                boardIds.push(new URL(request.url).searchParams.get('board_id'));
                return HttpResponse.json({ items: [taskFixture()], next_cursor: null });
            }),
        );
        open();
        expect(await screen.findByText('Loading tasks…')).toBeInTheDocument();
        const rows = within(await taskList()).getAllByRole('listitem');
        expect(rows).toHaveLength(1);
        expect(rows[0]).toHaveTextContent('#1 Build task service');
        expect(rows[0]).toHaveTextContent('Assigned to Dara');
        expect(rows[0]).toHaveTextContent('Due 2026-11-20');
        expect(within(rows[0]!).getByRole('link')).toHaveAttribute('href', `/workspaces/${WORKSPACE_ID}/tasks/${TASK_ID}`);
        expect(boardIds).toEqual([BOARD_ID]);
    });

    it('shows an unassigned task as unassigned', async () => {
        server.use(
            http.get(tasksUrl, () =>
                HttpResponse.json({ items: [taskFixture({ assignee: null, due_date: null })], next_cursor: null }),
            ),
        );
        open();
        const row = within(await taskList()).getAllByRole('listitem')[0]!;
        expect(row).toHaveTextContent('Unassigned');
        expect(row).not.toHaveTextContent('Due');
    });

    it('shows an empty state', async () => {
        open();
        expect(await screen.findByRole('heading', { name: 'No tasks' })).toBeInTheDocument();
        expect(screen.getByText('There are no tasks in Todo yet.')).toBeInTheDocument();
    });

    it('loads the next page with the cursor and keeps the first page', async () => {
        const cursors: (string | null)[] = [];
        server.use(
            http.get(tasksUrl, ({ request }) => {
                const cursor = new URL(request.url).searchParams.get('cursor');
                cursors.push(cursor);
                if (cursor === null) {
                    return HttpResponse.json({ items: [taskFixture()], next_cursor: 'page-2' });
                }
                return HttpResponse.json({
                    items: [taskFixture({ id: '00000000-0000-4000-8000-000000000020', number: 2, title: 'Second task' })],
                    next_cursor: null,
                });
            }),
        );
        const user = userEvent.setup();
        open();
        await taskList();
        await user.click(screen.getByRole('button', { name: 'Load more tasks' }));
        await waitFor(() => expect(within(screen.getByRole('list', { name: 'Tasks' })).getAllByRole('listitem')).toHaveLength(2));
        expect(cursors).toEqual([null, 'page-2']);
        expect(screen.queryByRole('button', { name: 'Load more tasks' })).not.toBeInTheDocument();
    });

    it('shows an error with a retry', async () => {
        let calls = 0;
        server.use(
            http.get(tasksUrl, () => {
                calls += 1;
                if (calls === 1) return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                return HttpResponse.json({ items: [taskFixture()], next_cursor: null });
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await taskList()).toBeInTheDocument();
    });
});

describe('TasksSection create', () => {
    it('creates a task from only a title, defaulting to this board, and reads the list again', async () => {
        const seen: { key: string | null; body: unknown }[] = [];
        let created = false;
        server.use(
            http.get(tasksUrl, () =>
                HttpResponse.json({
                    items: created ? [taskFixture({ title: 'Write docs', assignee: null })] : [],
                    next_cursor: null,
                }),
            ),
            http.post(tasksUrl, async ({ request }) => {
                seen.push({ key: request.headers.get('idempotency-key'), body: await request.json() });
                created = true;
                return HttpResponse.json(taskFixture({ title: 'Write docs', assignee: null }), { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No tasks' });

        await user.type(within(form()).getByLabelText('Title'), '  Write docs  ');
        await user.click(within(form()).getByRole('button', { name: 'Create task' }));

        expect(await screen.findByText('Task #1 “Write docs” created in Todo.')).toBeInTheDocument();
        expect(within(await taskList()).getAllByRole('listitem')).toHaveLength(1);
        expect(within(form()).getByLabelText('Title')).toHaveValue('');
        expect(seen).toHaveLength(1);
        expect(seen[0]?.key).toBeTruthy();
        expect(seen[0]?.body).toEqual({
            board_id: BOARD_ID,
            title: 'Write docs',
            description: '',
            assignee_id: null,
            start_date: null,
            due_date: null,
        });
    });

    it('offers only current members as assignees, and sends the chosen one with dates and another board', async () => {
        let body: unknown;
        server.use(
            http.post(tasksUrl, async ({ request }) => {
                body = await request.json();
                return HttpResponse.json(taskFixture({ board_id: DONE_ID }), { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No tasks' });
        await waitFor(() => expect(memberReads).toBeGreaterThan(0));

        const assignee = within(form()).getByLabelText('Assignee');
        await waitFor(() =>
            expect(within(assignee).getAllByRole('option').map((option) => option.textContent)).toEqual([
                'Unassigned',
                'Example Owner',
                'Dara',
            ]),
        );
        await user.type(within(form()).getByLabelText('Title'), 'Ship it');
        await user.type(within(form()).getByLabelText('Description'), 'Plain text');
        await user.selectOptions(assignee, 'Dara');
        await user.selectOptions(within(form()).getByLabelText('Board'), 'Done');
        fireEvent.change(within(form()).getByLabelText('Start date'), { target: { value: '2026-12-01' } });
        fireEvent.change(within(form()).getByLabelText('Due date'), { target: { value: '2026-12-05' } });
        await user.click(within(form()).getByRole('button', { name: 'Create task' }));

        expect(await screen.findByText(/created in Done\./)).toBeInTheDocument();
        expect(body).toEqual({
            board_id: DONE_ID,
            title: 'Ship it',
            description: 'Plain text',
            assignee_id: MEMBER_ID,
            start_date: '2026-12-01',
            due_date: '2026-12-05',
        });
    });

    it('checks the contract bounds and date order without calling the server', async () => {
        let posts = 0;
        server.use(
            http.post(tasksUrl, () => {
                posts += 1;
                return HttpResponse.json(taskFixture(), { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No tasks' });

        await user.click(within(form()).getByRole('button', { name: 'Create task' }));
        expect(screen.getByText('Enter a title.')).toBeInTheDocument();

        fireEvent.change(within(form()).getByLabelText('Title'), { target: { value: 'x'.repeat(201) } });
        fireEvent.change(within(form()).getByLabelText('Description'), { target: { value: 'y'.repeat(5001) } });
        await user.click(within(form()).getByRole('button', { name: 'Create task' }));
        expect(screen.getByText('Use 200 characters or fewer.')).toBeInTheDocument();
        expect(screen.getByText('Use 5000 characters or fewer.')).toBeInTheDocument();

        fireEvent.change(within(form()).getByLabelText('Title'), { target: { value: 'Fine' } });
        fireEvent.change(within(form()).getByLabelText('Description'), { target: { value: '' } });
        fireEvent.change(within(form()).getByLabelText('Start date'), { target: { value: '2026-12-10' } });
        fireEvent.change(within(form()).getByLabelText('Due date'), { target: { value: '2026-12-01' } });
        await user.click(within(form()).getByRole('button', { name: 'Create task' }));
        expect(screen.getByText(/on or after the start date/)).toBeInTheDocument();
        expect(posts).toBe(0);
    });

    it('shows the server’s reason for a rejected assignee and re-reads the members', async () => {
        server.use(
            http.post(tasksUrl, () =>
                HttpResponse.json(
                    errorBodyFixture('VALIDATION_FAILED', [{ field: 'assignee_id', reason: 'That user is not a member.' }]),
                    { status: 422 },
                ),
            ),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No tasks' });
        await waitFor(() => expect(memberReads).toBe(1));
        await user.type(within(form()).getByLabelText('Title'), 'Task');
        await user.click(within(form()).getByRole('button', { name: 'Create task' }));

        expect(await screen.findByText('That user is not a member.')).toBeInTheDocument();
        await waitFor(() => expect(memberReads).toBe(2));
        expect(within(form()).getByLabelText('Title')).toHaveValue('Task');
    });

    it('retries an unclear failure with the same key and shows the task once', async () => {
        const keys: (string | null)[] = [];
        let created = false;
        server.use(
            http.get(tasksUrl, () =>
                HttpResponse.json({ items: created ? [taskFixture({ title: 'Once' })] : [], next_cursor: null }),
            ),
            http.post(tasksUrl, ({ request }) => {
                keys.push(request.headers.get('idempotency-key'));
                if (keys.length === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                }
                created = true;
                return HttpResponse.json(taskFixture({ title: 'Once' }), { status: 201 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No tasks' });
        await user.type(within(form()).getByLabelText('Title'), 'Once');
        await user.click(within(form()).getByRole('button', { name: 'Create task' }));
        expect(await screen.findByText(/will not create a duplicate/)).toBeInTheDocument();

        await user.click(within(form()).getByRole('button', { name: 'Create task' }));
        expect(await screen.findByText(/created in Todo\./)).toBeInTheDocument();
        expect(keys[0]).toBe(keys[1]);
        expect(within(await taskList()).getAllByRole('listitem')).toHaveLength(1);
    });

    it('re-reads the boards when the destination board was deleted', async () => {
        server.use(
            http.post(tasksUrl, () =>
                HttpResponse.json(
                    errorBodyFixture('VALIDATION_FAILED', [{ field: 'board_id', reason: 'That board no longer exists.' }]),
                    { status: 422 },
                ),
            ),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'No tasks' });
        const before = boardReads;
        await user.type(within(form()).getByLabelText('Title'), 'Task');
        await user.click(within(form()).getByRole('button', { name: 'Create task' }));
        expect(await screen.findByText('That board no longer exists.')).toBeInTheDocument();
        await waitFor(() => expect(boardReads).toBeGreaterThan(before));
    });
});

describe('TasksSection move', () => {
    const withTask = () =>
        server.use(http.get(tasksUrl, () => HttpResponse.json({ items: [taskFixture()], next_cursor: null })));

    it('moves a task with If-Match and reads the list again', async () => {
        let moved = false;
        let seen: { ifMatch: string | null; body: unknown } | undefined;
        server.use(
            http.get(tasksUrl, () =>
                HttpResponse.json({ items: moved ? [] : [taskFixture()], next_cursor: null }),
            ),
            http.patch(`${tasksUrl}/${TASK_ID}`, async ({ request }) => {
                seen = { ifMatch: request.headers.get('if-match'), body: await request.json() };
                moved = true;
                return HttpResponse.json(taskFixture({ board_id: DONE_ID, version: 2 }));
            }),
        );
        const user = userEvent.setup();
        open();
        await taskList();
        const select = screen.getByLabelText('Destination board for task #1');
        expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual(['Move to…', 'Done']);
        expect(screen.getByRole('button', { name: 'Move task #1' })).toBeDisabled();

        await user.selectOptions(select, 'Done');
        await user.click(screen.getByRole('button', { name: 'Move task #1' }));

        expect(await screen.findByText('Task #1 moved to Done.')).toBeInTheDocument();
        expect(await screen.findByRole('heading', { name: 'No tasks' })).toBeInTheDocument();
        expect(seen).toEqual({ ifMatch: '"1"', body: { board_id: DONE_ID } });
    });

    it('after a stale version, shows the latest list', async () => {
        withTask();
        server.use(
            http.patch(`${tasksUrl}/${TASK_ID}`, () =>
                HttpResponse.json(errorBodyFixture('VERSION_CONFLICT'), { status: 412 }),
            ),
        );
        const user = userEvent.setup();
        open();
        await taskList();
        await user.selectOptions(screen.getByLabelText('Destination board for task #1'), 'Done');
        await user.click(screen.getByRole('button', { name: 'Move task #1' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('changed by someone else');
        expect(screen.getByRole('alert')).toHaveTextContent('latest list is shown');
    });

    it('shows the server’s reason when the destination board was deleted, and re-reads the boards', async () => {
        withTask();
        server.use(
            http.patch(`${tasksUrl}/${TASK_ID}`, () =>
                HttpResponse.json(
                    errorBodyFixture('VALIDATION_FAILED', [{ field: 'board_id', reason: 'That board no longer exists.' }]),
                    { status: 422 },
                ),
            ),
        );
        const user = userEvent.setup();
        open();
        await taskList();
        const before = boardReads;
        await user.selectOptions(screen.getByLabelText('Destination board for task #1'), 'Done');
        await user.click(screen.getByRole('button', { name: 'Move task #1' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('That board no longer exists.');
        await waitFor(() => expect(boardReads).toBeGreaterThan(before));
    });

    it('says so when the task is already gone', async () => {
        withTask();
        server.use(
            http.patch(`${tasksUrl}/${TASK_ID}`, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })),
        );
        const user = userEvent.setup();
        open();
        await taskList();
        await user.selectOptions(screen.getByLabelText('Destination board for task #1'), 'Done');
        await user.click(screen.getByRole('button', { name: 'Move task #1' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Task #1 is no longer available.');
    });
});
