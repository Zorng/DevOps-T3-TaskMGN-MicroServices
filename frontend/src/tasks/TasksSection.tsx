import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router';
import { isForbidden, isNotFound } from '../api/errors';
import { describeError, fieldReason } from '../api/messages';
import { isVersionConflict } from '../api/mutations';
import { usePageLoader } from '../api/usePageLoader';
import { useAuth } from '../auth/AuthProvider';
import type { Board } from '../boards/boardsApi';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { etagForVersion, listMembers, type Member, type Workspace } from '../workspaces/workspacesApi';
import { TaskForm } from './TaskForm';
import {
    createTaskAttempt,
    moveTask,
    needsReread,
    serverFieldErrors,
    taskRoute,
    tasksPath,
    type Task,
    type TaskFieldErrors,
    type TaskInput,
} from './tasksApi';

interface TasksSectionProps {
    workspace: Workspace;
    board: Board;
    boards: Board[];
    /** Re-read the board page (boards, role) after a refusal that may mean something changed. */
    onStale: () => Promise<void>;
}

interface PendingCreate {
    key: string;
    create: ReturnType<typeof createTaskAttempt>;
}

type Notice = { kind: 'ok' | 'error'; text: string };

function noticeFromState(state: unknown): Notice | null {
    if (typeof state === 'object' && state !== null && 'notice' in state && typeof state.notice === 'string') {
        return { kind: 'ok', text: state.notice };
    }
    return null;
}

function TaskRow({
    task,
    boards,
    busy,
    onMove,
}: {
    task: Task;
    boards: Board[];
    busy: boolean;
    onMove: (task: Task, boardId: string) => void;
}) {
    const [target, setTarget] = useState('');
    const others = boards.filter((board) => board.id !== task.board_id);
    return (
        <li>
            <Link to={taskRoute(task.workspace_id, task.id)}>{`#${task.number} ${task.title}`}</Link>
            {' · '}
            <span>{task.assignee ? `Assigned to ${task.assignee.display_name}` : 'Unassigned'}</span>
            {task.due_date && <span>{` · Due ${task.due_date}`}</span>}
            <select
                aria-label={`Destination board for task #${task.number}`}
                value={target}
                disabled={busy || others.length === 0}
                onChange={(event) => setTarget(event.target.value)}
            >
                <option value="">Move to…</option>
                {others.map((board) => (
                    <option key={board.id} value={board.id}>
                        {board.name}
                    </option>
                ))}
            </select>
            <button
                type="button"
                aria-label={`Move task #${task.number}`}
                disabled={busy || target === ''}
                onClick={() => onMove(task, target)}
            >
                Move
            </button>
        </li>
    );
}

export function TasksSection({ workspace, board, boards, onStale }: TasksSectionProps) {
    const { client } = useAuth();
    const location = useLocation();
    // A new board means a new loader: a cursor only works for the board filter that issued it.
    const { items, status, error, hasMore, loadMore, reload } = usePageLoader<Task>(client, {
        path: tasksPath(workspace.id),
        query: { board_id: board.id },
    });

    const [members, setMembers] = useState<Member[] | null>(null);
    const [membersError, setMembersError] = useState<string | null>(null);
    const [memberReloads, setMemberReloads] = useState(0);
    const [notice, setNotice] = useState<Notice | null>(() => noticeFromState(location.state));
    const [serverErrors, setServerErrors] = useState<TaskFieldErrors>({});
    const [formMessage, setFormMessage] = useState<string | null>(null);
    const [formVersion, setFormVersion] = useState(0);
    const [creating, setCreating] = useState(false);
    const [movingId, setMovingId] = useState<string | null>(null);
    const pending = useRef<PendingCreate | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        void listMembers(client, workspace.id, controller.signal).then(
            (loaded) => {
                if (controller.signal.aborted) return;
                setMembers(loaded);
                setMembersError(null);
            },
            (caught: unknown) => {
                if (!controller.signal.aborted) setMembersError(describeError(caught));
            },
        );
        return () => controller.abort();
    }, [client, workspace.id, memberReloads]);
    const reloadMembers = useCallback(() => setMemberReloads((n) => n + 1), []);

    async function handleCreate(input: TaskInput) {
        if (creating) return;
        setCreating(true);
        setServerErrors({});
        setFormMessage(null);
        setNotice(null);

        const key = JSON.stringify(input);
        let current = pending.current;
        if (!current || current.key !== key || current.create.attempt.state === 'rejected') {
            current = { key, create: createTaskAttempt(client, workspace.id, input) };
            pending.current = current;
        }

        try {
            const created = await current.create.send();
            pending.current = null;
            const destination = boards.find((item) => item.id === created.task.board_id)?.name ?? 'the board';
            setNotice({
                kind: 'ok',
                text: `Task #${created.task.number} “${created.task.title}” created in ${destination}.`,
            });
            setFormVersion((n) => n + 1); // a fresh, empty form
            reload(); // read the list from the server instead of adding to it, so a retry cannot show twice
        } catch (caught) {
            const fields = serverFieldErrors(caught);
            setServerErrors(fields);
            const text = describeError(caught, 'The task could not be created.');
            setFormMessage(
                current.create.attempt.state === 'retryable'
                    ? `${text} Press “Create task” again to retry. It will not create a duplicate.`
                    : text,
            );
            // The server decides: a former member as assignee, a deleted board, or lost access.
            if (fields.assignee_id) reloadMembers();
            if (fields.board_id || isNotFound(caught) || isForbidden(caught)) await onStale();
        } finally {
            setCreating(false);
        }
    }

    async function handleMove(task: Task, boardId: string) {
        if (movingId) return;
        const destination = boards.find((item) => item.id === boardId)?.name ?? 'the board';
        setMovingId(task.id);
        setNotice(null);
        try {
            await moveTask(client, workspace.id, task.id, boardId, etagForVersion(task.version));
            setNotice({ kind: 'ok', text: `Task #${task.number} moved to ${destination}.` });
            reload();
        } catch (caught) {
            if (needsReread(caught)) reload();
            const text =
                fieldReason(caught, 'board_id') ??
                (isNotFound(caught)
                    ? `Task #${task.number} is no longer available.`
                    : describeError(caught, `Task #${task.number} could not be moved.`));
            setNotice({ kind: 'error', text: isVersionConflict(caught) ? `${text} The latest list is shown.` : text });
            if (isNotFound(caught) || isForbidden(caught) || fieldReason(caught, 'board_id')) await onStale();
        } finally {
            setMovingId(null);
        }
    }

    return (
        <section aria-labelledby="tasks-title">
            <h2 id="tasks-title">Tasks</h2>
            {notice && <p role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</p>}

            {status === 'loading' && items.length === 0 && <LoadingState label="Loading tasks…" />}
            {status === 'error' && items.length === 0 && error && (
                <ErrorState message={describeError(error)} onRetry={loadMore} />
            )}
            {status === 'ready' && items.length === 0 && (
                <EmptyState title="No tasks" message={`There are no tasks in ${board.name} yet.`} />
            )}
            {items.length > 0 && (
                <ul aria-label="Tasks">
                    {items.map((task) => (
                        <TaskRow key={task.id} task={task} boards={boards} busy={movingId !== null} onMove={handleMove} />
                    ))}
                </ul>
            )}
            {items.length > 0 && status === 'loading' && <LoadingState label="Loading more tasks…" />}
            {items.length > 0 && status === 'error' && error && (
                <ErrorState message={describeError(error)} onRetry={loadMore} retryLabel="Try loading more again" />
            )}
            {status === 'ready' && hasMore && (
                <button type="button" onClick={loadMore}>
                    Load more tasks
                </button>
            )}

            <h3>New task</h3>
            {membersError && (
                <p>
                    {`Members could not be loaded, so tasks cannot be assigned right now. ${membersError} `}
                    <button type="button" onClick={reloadMembers}>
                        Reload members
                    </button>
                </p>
            )}
            <TaskForm
                key={formVersion}
                idPrefix="new-task"
                label="Create a task"
                initial={{ boardId: board.id, title: '', description: '', assigneeId: '', startDate: '', dueDate: '' }}
                boards={boards}
                members={members ?? []}
                submitLabel="Create task"
                busy={creating}
                serverErrors={serverErrors}
                onSubmit={(input) => void handleCreate(input)}
            >
                {formMessage && <p role="alert">{formMessage}</p>}
            </TaskForm>
        </section>
    );
}
