import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import type { ApiClient } from '../api/client';
import { isForbidden, isNotFound } from '../api/errors';
import { describeError, fieldReason } from '../api/messages';
import { isVersionConflict } from '../api/mutations';
import { useAuth } from '../auth/AuthProvider';
import { CommentsSection } from '../comments/CommentsSection';
import { boardRoute, listBoards, type Board } from '../boards/boardsApi';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { getWorkspace, listMembers, type Member, type Workspace } from '../workspaces/workspacesApi';
import { TaskForm } from './TaskForm';
import {
    buildTaskPatch,
    deleteTask,
    getTask,
    moveTask,
    needsReread,
    serverFieldErrors,
    updateTask,
    valuesFromTask,
    type Task,
    type TaskFieldErrors,
    type TaskInput,
    type VersionedTask,
} from './tasksApi';

type View =
    | { kind: 'loading' }
    | { kind: 'ready'; workspace: Workspace; task: Task; etag: string; boards: Board[]; members: Member[] }
    | { kind: 'task-gone' }
    | { kind: 'workspace-gone' }
    | { kind: 'denied' }
    | { kind: 'error'; message: string };

const LATEST_SHOWN = ' The latest version of the task is shown.';

function classify(error: unknown, target: 'workspace' | 'task'): View {
    if (isNotFound(error)) return { kind: target === 'task' ? 'task-gone' : 'workspace-gone' };
    if (isForbidden(error)) return { kind: 'denied' };
    return { kind: 'error', message: describeError(error) };
}

/**
 * Reads the workspace (access), the task (its version), the boards (names and destinations) and the members (assignees).
 * A 404 on the task alone means the task, or its board, is gone; a 404 on the workspace means the whole workspace is.
 */
async function loadView(client: ApiClient, workspaceId: string, taskId: string, signal?: AbortSignal): Promise<View> {
    const [workspace, task, boards, members] = await Promise.allSettled([
        getWorkspace(client, workspaceId, signal),
        getTask(client, workspaceId, taskId, signal),
        listBoards(client, workspaceId, signal),
        listMembers(client, workspaceId, signal),
    ]);
    if (workspace.status === 'rejected') return classify(workspace.reason, 'workspace');
    if (task.status === 'rejected') return classify(task.reason, 'task');
    if (boards.status === 'rejected') return classify(boards.reason, 'workspace');
    if (members.status === 'rejected') return classify(members.reason, 'workspace');
    return {
        kind: 'ready',
        workspace: workspace.value.workspace,
        task: task.value.task,
        etag: task.value.etag,
        boards: boards.value,
        members: members.value,
    };
}

interface ControlProps {
    workspaceId: string;
    task: Task;
    etag: string;
    boards: Board[];
    /** Re-read everything after a conflict, an unclear result or a change of access. */
    onStale: () => Promise<void>;
}

type Message = { kind: 'ok' | 'error'; text: string };

function MessageLine({ message }: { message: Message | null }) {
    if (!message) return null;
    return <p role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>;
}

function EditTaskForm({
    workspaceId,
    task,
    etag,
    members,
    onStale,
    onSaved,
}: ControlProps & { members: Member[]; onSaved: (updated: VersionedTask) => void }) {
    const { client } = useAuth();
    const [busy, setBusy] = useState(false);
    const [serverErrors, setServerErrors] = useState<TaskFieldErrors>({});
    const [message, setMessage] = useState<Message | null>(null);

    async function handleSubmit(input: TaskInput) {
        if (busy) return;
        const patch = buildTaskPatch(task, input);
        if (Object.keys(patch).length === 0) {
            setServerErrors({});
            setMessage({ kind: 'error', text: 'There are no changes to save.' });
            return;
        }
        setBusy(true);
        setServerErrors({});
        setMessage(null);
        try {
            const updated = await updateTask(client, workspaceId, task.id, patch, etag);
            setMessage({ kind: 'ok', text: 'Task saved.' });
            onSaved(updated);
        } catch (caught) {
            const fields = serverFieldErrors(caught);
            setServerErrors(fields);
            // A former member as assignee also means the member list on screen is out of date.
            if (needsReread(caught) || fields.assignee_id) await onStale();
            const text = describeError(caught, 'The task could not be saved.');
            setMessage({ kind: 'error', text: isVersionConflict(caught) ? `${text}${LATEST_SHOWN}` : text });
        } finally {
            setBusy(false);
        }
    }

    return (
        <TaskForm
            idPrefix="edit-task"
            label="Edit task"
            initial={valuesFromTask(task)}
            boards={null}
            members={members}
            currentAssignee={task.assignee}
            submitLabel="Save task"
            busy={busy}
            serverErrors={serverErrors}
            onSubmit={(input) => void handleSubmit(input)}
        >
            <MessageLine message={message} />
        </TaskForm>
    );
}

function MoveTaskPanel({
    workspaceId,
    task,
    etag,
    boards,
    onStale,
    onMoved,
}: ControlProps & { onMoved: (updated: VersionedTask) => void }) {
    const { client } = useAuth();
    const others = boards.filter((board) => board.id !== task.board_id);
    const [target, setTarget] = useState('');
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState<Message | null>(null);

    async function handleMove() {
        if (busy || target === '') return;
        const destination = boards.find((board) => board.id === target)?.name ?? 'the board';
        setBusy(true);
        setMessage(null);
        try {
            const updated = await moveTask(client, workspaceId, task.id, target, etag);
            setTarget('');
            setMessage({ kind: 'ok', text: `Task moved to ${destination}.` });
            onMoved(updated);
        } catch (caught) {
            const reason = fieldReason(caught, 'board_id');
            if (needsReread(caught) || reason) await onStale();
            const text = reason ?? describeError(caught, 'The task could not be moved.');
            setMessage({ kind: 'error', text: isVersionConflict(caught) ? `${text}${LATEST_SHOWN}` : text });
        } finally {
            setBusy(false);
        }
    }

    return (
        <section aria-labelledby="move-title">
            <h2 id="move-title">Move to another board</h2>
            <label htmlFor="move-board">Destination board</label>
            <select
                id="move-board"
                value={target}
                disabled={busy || others.length === 0}
                onChange={(event) => setTarget(event.target.value)}
            >
                <option value="">Choose a board…</option>
                {others.map((board) => (
                    <option key={board.id} value={board.id}>
                        {board.name}
                    </option>
                ))}
            </select>
            <button type="button" disabled={busy || target === ''} onClick={() => void handleMove()}>
                Move task
            </button>
            {others.length === 0 && <p>This workspace has no other board to move the task to.</p>}
            <MessageLine message={message} />
        </section>
    );
}

function DeleteTaskPanel({
    workspaceId,
    task,
    etag,
    onStale,
    onDeleted,
}: ControlProps & { onDeleted: () => void }) {
    const { client } = useAuth();
    const [confirming, setConfirming] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const openButton = useRef<HTMLButtonElement>(null);
    const cancelButton = useRef<HTMLButtonElement>(null);
    const wasConfirming = useRef(false);

    // Move focus into the confirmation, and back to the trigger when it closes.
    useEffect(() => {
        if (confirming) cancelButton.current?.focus();
        else if (wasConfirming.current) openButton.current?.focus();
        wasConfirming.current = confirming;
    }, [confirming]);

    async function confirmDelete() {
        if (busy) return;
        setBusy(true);
        setError(null);
        try {
            await deleteTask(client, workspaceId, task.id, etag);
            onDeleted();
        } catch (caught) {
            if (needsReread(caught)) await onStale();
            const text = describeError(caught, 'The task could not be deleted.');
            setError(
                isVersionConflict(caught)
                    ? `${text}${LATEST_SHOWN} Confirm again if you still want to delete it.`
                    : text,
            );
        } finally {
            setBusy(false);
        }
    }

    if (!confirming) {
        return (
            <button type="button" ref={openButton} onClick={() => setConfirming(true)}>
                Delete task
            </button>
        );
    }

    return (
        <section aria-labelledby="delete-task-title">
            <h2 id="delete-task-title">{`Delete task #${task.number}?`}</h2>
            <p>
                This permanently deletes “{task.title}” and all of its comments. Nobody in the workspace will be able to
                open them again. This cannot be undone.
            </p>
            {error && <p role="alert">{error}</p>}
            <button
                type="button"
                ref={cancelButton}
                disabled={busy}
                onClick={() => {
                    setConfirming(false);
                    setError(null);
                }}
            >
                Cancel
            </button>
            <button type="button" disabled={busy} onClick={() => void confirmDelete()}>
                Yes, delete task
            </button>
        </section>
    );
}

export function TaskPage() {
    const { workspaceId, taskId } = useParams();
    const { client } = useAuth();
    const navigate = useNavigate();
    const [view, setView] = useState<View>({ kind: 'loading' });
    const [reloads, setReloads] = useState(0);

    useEffect(() => {
        if (!workspaceId || !taskId) {
            setView({ kind: 'task-gone' });
            return;
        }
        const controller = new AbortController();
        setView({ kind: 'loading' });
        void loadView(client, workspaceId, taskId, controller.signal).then((next) => {
            if (!controller.signal.aborted) setView(next);
        });
        return () => controller.abort();
    }, [client, workspaceId, taskId, reloads]);

    /** Re-read after a conflict or a 403/404. A failed re-read keeps what is on screen. */
    const refresh = useCallback(async () => {
        if (!workspaceId || !taskId) return;
        const next = await loadView(client, workspaceId, taskId);
        if (next.kind !== 'error') setView(next);
    }, [client, workspaceId, taskId]);

    if (!workspaceId || view.kind === 'loading') return <LoadingState label="Loading task…" />;
    if (view.kind === 'error') {
        return <ErrorState message={view.message} onRetry={() => setReloads((n) => n + 1)} />;
    }
    if (view.kind === 'workspace-gone') {
        return (
            <>
                <EmptyState
                    title="Workspace not available"
                    message="This workspace may have been deleted, or you may no longer have access to it."
                />
                <p>
                    <Link to="/workspaces">Back to workspaces</Link>
                </p>
            </>
        );
    }
    if (view.kind === 'task-gone' || view.kind === 'denied') {
        return (
            <>
                <EmptyState
                    title={view.kind === 'denied' ? 'Access denied' : 'Task not available'}
                    message={
                        view.kind === 'denied'
                            ? 'You do not have permission to open this task.'
                            : 'This task, or the board it was on, may have been deleted, or you may no longer have access to it.'
                    }
                />
                <p>
                    <Link to={`/workspaces/${encodeURIComponent(workspaceId)}`}>Back to the workspace</Link>
                </p>
            </>
        );
    }

    const { workspace, task, etag, boards, members } = view;
    const boardName = boards.find((board) => board.id === task.board_id)?.name ?? 'the board';
    const shared = { workspaceId: workspace.id, task, etag, boards, onStale: refresh };
    const accept = (updated: VersionedTask) =>
        setView({ ...view, task: updated.task, etag: updated.etag });

    return (
        <>
            <p>
                <Link to={boardRoute(workspace.id, task.board_id)}>{`Back to ${boardName}`}</Link>
            </p>
            <h1>{`#${task.number} ${task.title}`}</h1>
            <dl aria-label="Task details">
                <dt>Board</dt>
                <dd>{boardName}</dd>
                <dt>Assignee</dt>
                <dd>{task.assignee ? task.assignee.display_name : 'Unassigned'}</dd>
                <dt>Start date</dt>
                <dd>{task.start_date ?? 'Not set'}</dd>
                <dt>Due date</dt>
                <dd>{task.due_date ?? 'Not set'}</dd>
                <dt>Created by</dt>
                <dd>{task.creator.display_name}</dd>
                <dt>Created</dt>
                <dd>{task.created_at}</dd>
                <dt>Updated</dt>
                <dd>{task.updated_at}</dd>
            </dl>
            {task.description !== '' && <p>{task.description}</p>}

            <CommentsSection
                key={`comments-${task.id}`}
                workspaceId={workspace.id}
                taskId={task.id}
                members={members}
                onStale={refresh}
            />
            <EditTaskForm key={task.id} {...shared} members={members} onSaved={accept} />
            <MoveTaskPanel key={`move-${task.id}`} {...shared} onMoved={accept} />
            <DeleteTaskPanel
                key={`delete-${task.id}`}
                {...shared}
                onDeleted={() =>
                    navigate(boardRoute(workspace.id, task.board_id), {
                        state: { notice: `Task #${task.number} deleted.` },
                    })
                }
            />
        </>
    );
}
