import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { createApiClient } from '../api/client';
import { TEST_API_BASE_URL } from '../test/handlers';
import { BOARD_ID, errorBodyFixture, MEMBER_ID, TASK_ID, taskFixture, WORKSPACE_ID } from '../test/fixtures';
import { server } from '../test/server';
import {
    buildTaskPatch,
    checkTaskForm,
    createTaskAttempt,
    deleteTask,
    getTask,
    isValidDate,
    moveTask,
    parseTask,
    TASK_DESCRIPTION_MAX,
    TASK_TITLE_MAX,
    taskRoute,
    updateTask,
    valuesFromTask,
    type TaskFormValues,
} from './tasksApi';

const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'test-token' });
const tasksUrl = `${TEST_API_BASE_URL}/api/v1/workspaces/${WORKSPACE_ID}/tasks`;
const taskUrl = `${tasksUrl}/${TASK_ID}`;

const form = (overrides: Partial<TaskFormValues> = {}): TaskFormValues => ({
    boardId: BOARD_ID,
    title: 'Write docs',
    description: '',
    assigneeId: '',
    startDate: '',
    dueDate: '',
    ...overrides,
});

describe('parseTask', () => {
    it('reads every fixed field', () => {
        const task = parseTask(taskFixture());
        expect(task).toMatchObject({
            number: 1,
            title: 'Build task service',
            board_id: BOARD_ID,
            assignee: { id: MEMBER_ID, display_name: 'Dara' },
            start_date: '2026-11-15',
            due_date: '2026-11-20',
            creator: { display_name: 'Example Owner' },
        });
    });

    it('accepts an unassigned task with no dates and an empty description', () => {
        const task = parseTask(taskFixture({ assignee: null, start_date: null, due_date: null, description: '' }));
        expect(task).toMatchObject({ assignee: null, start_date: null, due_date: null, description: '' });
    });

    it('rejects values that are not a task', () => {
        expect(parseTask({ id: 'x' })).toBeNull();
        expect(parseTask(taskFixture({ number: 0 }))).toBeNull();
        expect(parseTask(taskFixture({ assignee: { id: 'x' } }))).toBeNull();
        expect(parseTask(taskFixture({ due_date: 5 }))).toBeNull();
    });
});

describe('isValidDate', () => {
    it('accepts real calendar dates only', () => {
        expect(isValidDate('2026-11-20')).toBe(true);
        expect(isValidDate('2028-02-29')).toBe(true);
        expect(isValidDate('2026-02-29')).toBe(false);
        expect(isValidDate('2026-13-01')).toBe(false);
        expect(isValidDate('20-11-2026')).toBe(false);
        expect(isValidDate('')).toBe(false);
    });
});

describe('checkTaskForm', () => {
    it('needs only a title and a board, and turns empty optional fields into null', () => {
        expect(checkTaskForm(form({ title: '  Write docs  ' }))).toEqual({
            ok: true,
            input: {
                board_id: BOARD_ID,
                title: 'Write docs',
                description: '',
                assignee_id: null,
                start_date: null,
                due_date: null,
            },
        });
    });

    it('requires a title and a board', () => {
        const result = checkTaskForm(form({ title: '   ', boardId: '' }));
        expect(result).toMatchObject({ ok: false, errors: { title: 'Enter a title.', board_id: 'Choose a board.' } });
    });

    it('counts code points for the title and description limits', () => {
        expect(checkTaskForm(form({ title: '😀'.repeat(TASK_TITLE_MAX) })).ok).toBe(true);
        expect(checkTaskForm(form({ title: '😀'.repeat(TASK_TITLE_MAX + 1) }))).toMatchObject({
            ok: false,
            errors: { title: expect.stringContaining('200') },
        });
        expect(checkTaskForm(form({ description: 'x'.repeat(TASK_DESCRIPTION_MAX) })).ok).toBe(true);
        expect(checkTaskForm(form({ description: 'x'.repeat(TASK_DESCRIPTION_MAX + 1) }))).toMatchObject({
            ok: false,
            errors: { description: expect.stringContaining('5000') },
        });
    });

    it('does not trim the description', () => {
        const result = checkTaskForm(form({ description: '  keep  ' }));
        expect(result).toMatchObject({ ok: true, input: { description: '  keep  ' } });
    });

    it('rejects impossible dates and a start date after the due date, but allows equal dates', () => {
        expect(checkTaskForm(form({ startDate: '2026-02-30' }))).toMatchObject({
            ok: false,
            errors: { start_date: 'Enter a valid date.' },
        });
        expect(checkTaskForm(form({ startDate: '2026-11-21', dueDate: '2026-11-20' }))).toMatchObject({
            ok: false,
            errors: { due_date: expect.stringContaining('on or after the start date') },
        });
        expect(checkTaskForm(form({ startDate: '2026-11-20', dueDate: '2026-11-20' })).ok).toBe(true);
        expect(checkTaskForm(form({ startDate: '2026-11-20' })).ok).toBe(true);
    });
});

describe('buildTaskPatch', () => {
    const task = parseTask(taskFixture())!;
    const inputFor = (overrides: Partial<TaskFormValues>) => {
        const result = checkTaskForm({ ...valuesFromTask(task), ...overrides });
        if (!result.ok) throw new Error('expected valid input');
        return result.input;
    };

    it('is empty when nothing changed, so an unchanged assignee is never re-sent', () => {
        expect(buildTaskPatch(task, inputFor({}))).toEqual({});
    });

    it('contains only the changed fields', () => {
        expect(buildTaskPatch(task, inputFor({ title: 'New title' }))).toEqual({ title: 'New title' });
        expect(buildTaskPatch(task, inputFor({ description: '' }))).toEqual({ description: '' });
    });

    it('sends null to clear the assignee and dates', () => {
        expect(buildTaskPatch(task, inputFor({ assigneeId: '', startDate: '', dueDate: '' }))).toEqual({
            assignee_id: null,
            start_date: null,
            due_date: null,
        });
    });

    it('sets an assignee on an unassigned task', () => {
        const unassigned = parseTask(taskFixture({ assignee: null }))!;
        const result = checkTaskForm({ ...valuesFromTask(unassigned), assigneeId: MEMBER_ID });
        if (!result.ok) throw new Error('expected valid input');
        expect(buildTaskPatch(unassigned, result.input)).toEqual({ assignee_id: MEMBER_ID });
    });
});

describe('taskRoute', () => {
    it('names the workspace and the task only, because a task can change boards', () => {
        expect(taskRoute(WORKSPACE_ID, TASK_ID)).toBe(`/workspaces/${WORKSPACE_ID}/tasks/${TASK_ID}`);
    });
});

describe('task requests', () => {
    it('getTask returns the task and the server ETag, falling back to the quoted version', async () => {
        server.use(http.get(taskUrl, () => HttpResponse.json(taskFixture({ version: 4 }), { headers: { ETag: '"4"' } })));
        expect((await getTask(client, WORKSPACE_ID, TASK_ID)).etag).toBe('"4"');
        server.use(http.get(taskUrl, () => HttpResponse.json(taskFixture({ version: 6 }))));
        expect((await getTask(client, WORKSPACE_ID, TASK_ID)).etag).toBe('"6"');
    });

    it('getTask passes through a 404 for a deleted task', async () => {
        server.use(http.get(taskUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })));
        await expect(getTask(client, WORKSPACE_ID, TASK_ID)).rejects.toMatchObject({ status: 404 });
    });

    it('createTaskAttempt sends the body with an Idempotency-Key and reuses it on retry', async () => {
        const seen: { key: string | null; body: unknown }[] = [];
        server.use(
            http.post(tasksUrl, async ({ request }) => {
                seen.push({ key: request.headers.get('idempotency-key'), body: await request.json() });
                if (seen.length === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                }
                return HttpResponse.json(taskFixture(), { status: 201, headers: { ETag: '"1"' } });
            }),
        );
        const result = checkTaskForm(form());
        if (!result.ok) throw new Error('expected valid input');
        const create = createTaskAttempt(client, WORKSPACE_ID, result.input);
        await expect(create.send()).rejects.toMatchObject({ status: 503 });
        expect((await create.send()).task.number).toBe(1);
        expect(seen[0]?.key).toBe(seen[1]?.key);
        expect(seen[0]?.body).toEqual(result.input);
    });

    it('updateTask sends If-Match and only the patch; null stays null', async () => {
        let seen: { ifMatch: string | null; body: unknown } | undefined;
        server.use(
            http.patch(taskUrl, async ({ request }) => {
                seen = { ifMatch: request.headers.get('if-match'), body: await request.json() };
                return HttpResponse.json(taskFixture({ assignee: null, version: 2 }), { headers: { ETag: '"2"' } });
            }),
        );
        const result = await updateTask(client, WORKSPACE_ID, TASK_ID, { assignee_id: null }, '"1"');
        expect(seen).toEqual({ ifMatch: '"1"', body: { assignee_id: null } });
        expect(result.etag).toBe('"2"');
    });

    it('moveTask sends only the destination board', async () => {
        let body: unknown;
        server.use(
            http.patch(taskUrl, async ({ request }) => {
                body = await request.json();
                return HttpResponse.json(taskFixture({ version: 2 }));
            }),
        );
        await moveTask(client, WORKSPACE_ID, TASK_ID, 'board-2', '"1"');
        expect(body).toEqual({ board_id: 'board-2' });
    });

    it('deleteTask sends If-Match and accepts 204', async () => {
        let ifMatch: string | null = null;
        server.use(
            http.delete(taskUrl, ({ request }) => {
                ifMatch = request.headers.get('if-match');
                return new HttpResponse(null, { status: 204 });
            }),
        );
        await deleteTask(client, WORKSPACE_ID, TASK_ID, '"3"');
        expect(ifMatch).toBe('"3"');
    });
});
