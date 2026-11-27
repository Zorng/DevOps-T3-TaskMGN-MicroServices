import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useLocation } from 'react-router';
import { ApiError, isForbidden, isNotFound } from '../api/errors';
import { describeError, fieldReason } from '../api/messages';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import type { Workspace } from '../workspaces/workspacesApi';
import {
    BOARD_LIMIT,
    boardRoute,
    checkBoardName,
    createBoardAttempt,
    listBoards,
    type Board,
} from './boardsApi';

type ListState =
    | { kind: 'loading' }
    | { kind: 'ready'; boards: Board[] }
    | { kind: 'error'; message: string };

interface PendingCreate {
    name: string;
    create: ReturnType<typeof createBoardAttempt>;
}

interface BoardsSectionProps {
    workspace: Workspace;
    /** Re-read the workspace, for example after a 403 or 404 that may mean access changed. */
    onChanged: () => Promise<void>;
    /** The workspace is no longer accessible. */
    onGone: () => void;
}

function noticeFromState(state: unknown): string | null {
    if (typeof state === 'object' && state !== null && 'notice' in state && typeof state.notice === 'string') {
        return state.notice;
    }
    return null;
}

function createMessage(error: unknown, canRetry: boolean): string {
    const text = fieldReason(error, 'name') ?? describeError(error, 'The board could not be created.');
    return canRetry ? `${text} Press “Create board” again to retry. It will not create a duplicate.` : text;
}

export function BoardsSection({ workspace, onChanged, onGone }: BoardsSectionProps) {
    const { client } = useAuth();
    const location = useLocation();
    const [list, setList] = useState<ListState>({ kind: 'loading' });
    const [reloads, setReloads] = useState(0);
    const [name, setName] = useState('');
    const [formError, setFormError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(() => noticeFromState(location.state));
    const [creating, setCreating] = useState(false);
    const pending = useRef<PendingCreate | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        void listBoards(client, workspace.id, controller.signal).then(
            (boards) => {
                if (!controller.signal.aborted) setList({ kind: 'ready', boards });
            },
            (error: unknown) => {
                if (controller.signal.aborted) return;
                if (isNotFound(error)) onGone();
                else setList({ kind: 'error', message: describeError(error) });
            },
        );
        return () => controller.abort();
    }, [client, workspace.id, reloads, onGone]);

    // Show the loading state again only for a first load or an explicit retry, not for refreshes.
    const retry = useCallback(() => {
        setList({ kind: 'loading' });
        setReloads((n) => n + 1);
    }, []);
    const refresh = useCallback(() => setReloads((n) => n + 1), []);

    async function handleCreate(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (creating) return;
        const check = checkBoardName(name);
        if (!check.ok) {
            setFormError(check.message);
            return;
        }
        setFormError(null);
        setNotice(null);

        let current = pending.current;
        if (!current || current.name !== check.name || current.create.attempt.state === 'rejected') {
            current = { name: check.name, create: createBoardAttempt(client, workspace.id, check.name) };
            pending.current = current;
        }

        setCreating(true);
        try {
            const created = await current.create.send();
            pending.current = null;
            setName('');
            setNotice(`Board “${created.board.name}” created.`);
            refresh();
        } catch (caught) {
            setFormError(createMessage(caught, current.create.attempt.state === 'retryable'));
            // The server decides: re-read the boards (a full workspace, another member's change)
            // and the workspace (a lost role or a deleted workspace).
            if (caught instanceof ApiError && caught.code === 'BOARD_LIMIT_REACHED') refresh();
            if (isNotFound(caught) || isForbidden(caught)) await onChanged();
        } finally {
            setCreating(false);
        }
    }

    const boards = list.kind === 'ready' ? list.boards : [];
    const full = boards.length >= BOARD_LIMIT;

    return (
        <section aria-labelledby="boards-title">
            <h2 id="boards-title">Boards</h2>
            <p>
                Each board is a task status in this workspace, such as Todo or Done. A workspace can have up to{' '}
                {BOARD_LIMIT} boards. Owners and members can create and rename them; only the owner can delete one.
            </p>
            {notice && <p role="status">{notice}</p>}

            {list.kind === 'loading' && <LoadingState label="Loading boards…" />}
            {list.kind === 'error' && <ErrorState message={list.message} onRetry={retry} />}

            {list.kind === 'ready' && (
                <>
                    <p>{`${boards.length} of ${BOARD_LIMIT} boards`}</p>
                    {boards.length === 0 ? (
                        <EmptyState
                            title="No boards yet"
                            message="This workspace has no boards. Create the first one below, for example Todo."
                        />
                    ) : (
                        <ul aria-label="Boards">
                            {boards.map((board) => (
                                <li key={board.id}>
                                    <Link to={boardRoute(workspace.id, board.id)}>{board.name}</Link>
                                </li>
                            ))}
                        </ul>
                    )}

                    {full && (
                        <p>This workspace has the maximum of {BOARD_LIMIT} boards. Delete a board to create another.</p>
                    )}
                    <form onSubmit={handleCreate} noValidate aria-label="Create a board">
                        <label htmlFor="new-board-name">Board name</label>
                        <input
                            id="new-board-name"
                            value={name}
                            autoComplete="off"
                            disabled={full}
                            onChange={(event) => setName(event.target.value)}
                            aria-invalid={formError !== null}
                            aria-describedby={formError ? 'new-board-error' : undefined}
                        />
                        <button type="submit" disabled={creating || full}>
                            Create board
                        </button>
                        {formError && (
                            <p id="new-board-error" role="alert">
                                {formError}
                            </p>
                        )}
                    </form>
                </>
            )}
        </section>
    );
}
