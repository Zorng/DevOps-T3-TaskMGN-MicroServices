import type { ApiClient, ApiResponse } from '../api/client';
import { isForbidden, isNotFound } from '../api/errors';
import { CreateAttempt, deleteWithVersion, isAmbiguousOutcome, isVersionConflict, updateWithVersion } from '../api/mutations';
import { fieldReason } from '../api/messages';
import { invalidResponse, isRecord, positiveInt, str } from '../api/parse';
import { etagForVersion } from '../workspaces/workspacesApi';

export const TASK_TITLE_MAX = 200;
export const TASK_DESCRIPTION_MAX = 5000;

export interface UserSummary {
    id: string;
    display_name: string;
}

export interface Task {
    id: string;
    workspace_id: string;
    board_id: string;
    number: number;
    title: string;
    description: string;
    assignee: UserSummary | null;
    start_date: string | null;
    due_date: string | null;
    creator: UserSummary;
    version: number;
    created_at: string;
    updated_at: string;
}

/** A task plus the ETag to send back as If-Match on edit, move or delete. */
export interface VersionedTask {
    task: Task;
    etag: string;
}

function parseUser(value: unknown): UserSummary | null {
    if (!isRecord(value)) return null;
    const id = str(value['id']);
    const name = str(value['display_name']);
    return id && name ? { id, display_name: name } : null;
}

/** A string, null, or undefined when the value is neither (invalid). */
function nullableString(value: unknown): string | null | undefined {
    if (value === null) return null;
    return typeof value === 'string' && value !== '' ? value : undefined;
}

export function parseTask(value: unknown): Task | null {
    if (!isRecord(value)) return null;
    const id = str(value['id']);
    const workspaceId = str(value['workspace_id']);
    const boardId = str(value['board_id']);
    const number = positiveInt(value['number']);
    const title = str(value['title']);
    const description = value['description'];
    const creator = parseUser(value['creator']);
    const version = positiveInt(value['version']);
    const createdAt = str(value['created_at']);
    const updatedAt = str(value['updated_at']);
    const startDate = nullableString(value['start_date']);
    const dueDate = nullableString(value['due_date']);
    const rawAssignee = value['assignee'];
    const assignee = rawAssignee === null ? null : parseUser(rawAssignee);
    if (!id || !workspaceId || !boardId || !number || !title || typeof description !== 'string') return null;
    if (!creator || !version || !createdAt || !updatedAt) return null;
    if (startDate === undefined || dueDate === undefined) return null;
    if (rawAssignee !== null && assignee === null) return null;
    return {
        id,
        workspace_id: workspaceId,
        board_id: boardId,
        number,
        title,
        description,
        assignee,
        start_date: startDate,
        due_date: dueDate,
        creator,
        version,
        created_at: createdAt,
        updated_at: updatedAt,
    };
}

function toVersioned(result: ApiResponse<unknown>): VersionedTask {
    const task = parseTask(result.data);
    if (!task) throw invalidResponse(result.status, result.requestId);
    return { task, etag: result.etag ?? etagForVersion(task.version) };
}

/** What the form holds. Everything is a string; empty means "not set". */
export interface TaskFormValues {
    boardId: string;
    title: string;
    description: string;
    assigneeId: string;
    startDate: string;
    dueDate: string;
}

/** The validated, contract-shaped values (create body, and the source of an edit patch). */
export interface TaskInput {
    board_id: string;
    title: string;
    description: string;
    assignee_id: string | null;
    start_date: string | null;
    due_date: string | null;
}

export type TaskField = 'board_id' | 'title' | 'description' | 'assignee_id' | 'start_date' | 'due_date';
export type TaskFieldErrors = Partial<Record<TaskField, string>>;
export const TASK_FIELDS: TaskField[] = ['board_id', 'title', 'description', 'assignee_id', 'start_date', 'due_date'];

export function valuesFromTask(task: Task): TaskFormValues {
    return {
        boardId: task.board_id,
        title: task.title,
        description: task.description,
        assigneeId: task.assignee?.id ?? '',
        startDate: task.start_date ?? '',
        dueDate: task.due_date ?? '',
    };
}

/** True for a real calendar date written as YYYY-MM-DD. */
export function isValidDate(value: string): boolean {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) return false;
    const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
    const date = new Date(Date.UTC(year, month - 1, day));
    return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

export type TaskCheck = { ok: true; input: TaskInput } | { ok: false; errors: TaskFieldErrors };

/**
 * Applies the contract's text bounds (code points, title trimmed, description kept as typed),
 * calendar dates, and the start-before-due rule.
 */
export function checkTaskForm(values: TaskFormValues): TaskCheck {
    const errors: TaskFieldErrors = {};
    const title = values.title.trim();
    if (title === '') errors.title = 'Enter a title.';
    else if ([...title].length > TASK_TITLE_MAX) errors.title = `Use ${TASK_TITLE_MAX} characters or fewer.`;

    if ([...values.description].length > TASK_DESCRIPTION_MAX) {
        errors.description = `Use ${TASK_DESCRIPTION_MAX} characters or fewer.`;
    }
    if (values.boardId === '') errors.board_id = 'Choose a board.';

    const start = values.startDate;
    const due = values.dueDate;
    if (start !== '' && !isValidDate(start)) errors.start_date = 'Enter a valid date.';
    if (due !== '' && !isValidDate(due)) errors.due_date = 'Enter a valid date.';
    if (!errors.start_date && !errors.due_date && start !== '' && due !== '' && start > due) {
        errors.due_date = 'The due date must be on or after the start date.';
    }

    if (Object.keys(errors).length > 0) return { ok: false, errors };
    return {
        ok: true,
        input: {
            board_id: values.boardId,
            title,
            description: values.description,
            assignee_id: values.assigneeId === '' ? null : values.assigneeId,
            start_date: start === '' ? null : start,
            due_date: due === '' ? null : due,
        },
    };
}

/** Fields of a PATCH. Omitted = unchanged; null clears the assignee or a date. */
export type TaskPatch = Partial<Omit<TaskInput, 'board_id'>>;

/** Only what changed, so an unchanged assignee is omitted (no repeated assignment) and nothing else is touched. */
export function buildTaskPatch(task: Task, input: TaskInput): TaskPatch {
    const patch: TaskPatch = {};
    if (input.title !== task.title) patch.title = input.title;
    if (input.description !== task.description) patch.description = input.description;
    if (input.assignee_id !== (task.assignee?.id ?? null)) patch.assignee_id = input.assignee_id;
    if (input.start_date !== task.start_date) patch.start_date = input.start_date;
    if (input.due_date !== task.due_date) patch.due_date = input.due_date;
    return patch;
}

export const tasksPath = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}/tasks`;
const taskPath = (workspaceId: string, taskId: string) => `${tasksPath(workspaceId)}/${encodeURIComponent(taskId)}`;

/** The browser address of a task page. A task's board can change, so the address names only the workspace and task. */
export const taskRoute = (workspaceId: string, taskId: string) =>
    `/workspaces/${encodeURIComponent(workspaceId)}/tasks/${encodeURIComponent(taskId)}`;

export async function getTask(
    client: ApiClient,
    workspaceId: string,
    taskId: string,
    signal?: AbortSignal,
): Promise<VersionedTask> {
    return toVersioned(await client.get<unknown>(taskPath(workspaceId, taskId), { signal }));
}

/** One create intention = one idempotency key. Call send() again after an unclear failure. */
export function createTaskAttempt(client: ApiClient, workspaceId: string, input: TaskInput) {
    const attempt = new CreateAttempt<unknown>(client, tasksPath(workspaceId), input);
    return {
        attempt,
        async send(options: { signal?: AbortSignal } = {}): Promise<VersionedTask> {
            return toVersioned(await attempt.send(options));
        },
    };
}

export async function updateTask(
    client: ApiClient,
    workspaceId: string,
    taskId: string,
    patch: TaskPatch | { board_id: string },
    etag: string,
): Promise<VersionedTask> {
    return toVersioned(await updateWithVersion<unknown>(client, taskPath(workspaceId, taskId), patch, etag));
}

/** Moves within the workspace. Identity, number, description, assignee and dates are untouched by the server. */
export function moveTask(
    client: ApiClient,
    workspaceId: string,
    taskId: string,
    boardId: string,
    etag: string,
): Promise<VersionedTask> {
    return updateTask(client, workspaceId, taskId, { board_id: boardId }, etag);
}

export async function deleteTask(client: ApiClient, workspaceId: string, taskId: string, etag: string): Promise<void> {
    await deleteWithVersion(client, taskPath(workspaceId, taskId), etag);
}

/** After these the screen may be out of date, so read the server's state instead of re-sending. */
export function needsReread(error: unknown): boolean {
    return isVersionConflict(error) || isAmbiguousOutcome(error) || isNotFound(error) || isForbidden(error);
}

/** The server's reasons for individual fields of a rejected create or edit. */
export function serverFieldErrors(error: unknown): TaskFieldErrors {
    const errors: TaskFieldErrors = {};
    for (const field of TASK_FIELDS) {
        const reason = fieldReason(error, field);
        if (reason) errors[field] = reason;
    }
    return errors;
}
