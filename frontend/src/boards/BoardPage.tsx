import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, NavLink, useNavigate, useParams } from 'react-router';
import type { ApiClient } from '../api/client';
import { isForbidden, isNotFound } from '../api/errors';
import { describeError, fieldReason } from '../api/messages';
import { isAmbiguousOutcome, isVersionConflict } from '../api/mutations';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { TasksSection } from '../tasks/TasksSection';
import { getWorkspace, type Workspace } from '../workspaces/workspacesApi';
import {
    boardRoute,
    checkBoardName,
    deleteBoard,
    getBoard,
    listBoards,
    renameBoard,
    type Board,
    type VersionedBoard,
} from './boardsApi';

type View =
    | { kind: 'loading' }
    | { kind: 'ready'; workspace: Workspace; board: Board; boards: Board[]; etag: string }
    | { kind: 'board-gone' }
    | { kind: 'workspace-gone' }
    | { kind: 'denied' }
    | { kind: 'error'; message: string };

const LATEST_SHOWN = ' The latest version of the board is shown.';

/** Turns a failed request into the screen to show. `target` says what a 404 means. */
function classify(error: unknown, target: 'workspace' | 'board'): View {
    if (isNotFound(error)) return { kind: target === 'board' ? 'board-gone' : 'workspace-gone' };
    if (isForbidden(error)) return { kind: 'denied' };
    return { kind: 'error', message: describeError(error) };
}

/**
 * Reads the workspace (for the role), the board (for its version) and the board list (for navigation).
 * A 404 on the board alone means the board is gone; a 404 on the workspace means the whole workspace is.
 */
async function loadView(
    client: ApiClient,
    workspaceId: string,
    boardId: string,
    signal?: AbortSignal,
): Promise<View> {
    const [workspace, board, boards] = await Promise.allSettled([
        getWorkspace(client, workspaceId, signal),
        getBoard(client, workspaceId, boardId, signal),
        listBoards(client, workspaceId, signal),
    ]);
    if (workspace.status === 'rejected') return classify(workspace.reason, 'workspace');
    if (board.status === 'rejected') return classify(board.reason, 'board');
    if (boards.status === 'rejected') return classify(boards.reason, 'workspace');
    return {
        kind: 'ready',
        workspace: workspace.value.workspace,
        board: board.value.board,
        boards: boards.value,
        etag: board.value.etag,
    };
}

interface ControlProps {
    workspaceId: string;
    board: Board;
    etag: string;
    /** Re-read everything after a conflict, an unclear result or a change of access. */
    onStale: () => Promise<void>;
}

/** After these the screen may be out of date, so read the server's state instead of re-sending. */
function needsReread(error: unknown): boolean {
    return isVersionConflict(error) || isAmbiguousOutcome(error) || isNotFound(error) || isForbidden(error);
}

function RenameBoardForm({
    workspaceId,
    board,
    etag,
    onStale,
    onRenamed,
}: ControlProps & { onRenamed: (updated: VersionedBoard) => void }) {
    const { client } = useAuth();
    const [name, setName] = useState(board.name);
    const [message, setMessage] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null);
    const [busy, setBusy] = useState(false);

    async function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (busy) return;
        const check = checkBoardName(name);
        if (!check.ok) {
            setMessage({ kind: 'error', text: check.message });
            return;
        }
        if (check.name === board.name) {
            setMessage({ kind: 'error', text: 'Enter a different name to rename the board.' });
            return;
        }
        setBusy(true);
        setMessage(null);
        try {
            const updated = await renameBoard(client, workspaceId, board.id, check.name, etag);
            setName(updated.board.name);
            setMessage({ kind: 'ok', text: 'Board renamed.' });
            onRenamed(updated);
        } catch (error) {
            const reread = needsReread(error);
            if (reread) await onStale();
            const text = fieldReason(error, 'name') ?? describeError(error, 'The board could not be renamed.');
            setMessage({ kind: 'error', text: isVersionConflict(error) ? `${text}${LATEST_SHOWN}` : text });
        } finally {
            setBusy(false);
        }
    }

    return (
        <form onSubmit={handleSubmit} noValidate aria-label="Rename board">
            <label htmlFor="rename-board">Board name</label>
            <input
                id="rename-board"
                value={name}
                autoComplete="off"
                onChange={(event) => setName(event.target.value)}
                aria-invalid={message?.kind === 'error'}
                aria-describedby={message ? 'rename-board-message' : undefined}
            />
            <button type="submit" disabled={busy}>
                Rename board
            </button>
            {message && (
                <p id="rename-board-message" role={message.kind === 'error' ? 'alert' : 'status'}>
                    {message.text}
                </p>
            )}
        </form>
    );
}

function DeleteBoardPanel({
    workspaceId,
    board,
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
            await deleteBoard(client, workspaceId, board.id, etag);
            onDeleted();
        } catch (caught) {
            const reread = needsReread(caught);
            if (reread) await onStale();
            const text = describeError(caught, 'The board could not be deleted.');
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
                Delete board
            </button>
        );
    }

    return (
        <section aria-labelledby="delete-board-title">
            <h2 id="delete-board-title">Delete “{board.name}”?</h2>
            <p>
                This permanently deletes the board and every task and comment in it. Nobody in the workspace will be
                able to open them again. This cannot be undone.
            </p>
            <p>Deleting it frees one of the workspace’s board slots.</p>
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
                Yes, delete board
            </button>
        </section>
    );
}

export function BoardPage() {
    const { workspaceId, boardId } = useParams();
    const { client } = useAuth();
    const navigate = useNavigate();
    const [view, setView] = useState<View>({ kind: 'loading' });
    const [reloads, setReloads] = useState(0);

    useEffect(() => {
        if (!workspaceId || !boardId) {
            setView({ kind: 'board-gone' });
            return;
        }
        const controller = new AbortController();
        setView({ kind: 'loading' });
        void loadView(client, workspaceId, boardId, controller.signal).then((next) => {
            if (!controller.signal.aborted) setView(next);
        });
        return () => controller.abort();
    }, [client, workspaceId, boardId, reloads]);

    /** Re-read after a conflict or a 403/404. A failed re-read keeps what is on screen. */
    const refresh = useCallback(async () => {
        if (!workspaceId || !boardId) return;
        const next = await loadView(client, workspaceId, boardId);
        if (next.kind !== 'error') setView(next);
    }, [client, workspaceId, boardId]);

    if (!workspaceId || view.kind === 'loading') return <LoadingState label="Loading board…" />;
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
    if (view.kind === 'board-gone' || view.kind === 'denied') {
        return (
            <>
                <EmptyState
                    title={view.kind === 'denied' ? 'Access denied' : 'Board not available'}
                    message={
                        view.kind === 'denied'
                            ? 'You do not have permission to open this board.'
                            : 'This board may have been deleted, or you may no longer have access to it.'
                    }
                />
                <p>
                    <Link to={`/workspaces/${encodeURIComponent(workspaceId)}`}>Back to the workspace</Link>
                </p>
            </>
        );
    }

    const { workspace, board, boards, etag } = view;
    const isOwner = workspace.my_role === 'owner';

    return (
        <>
            <p>
                <Link to={`/workspaces/${encodeURIComponent(workspace.id)}`}>{`Back to ${workspace.name}`}</Link>
            </p>
            <nav aria-label="Boards in this workspace">
                <ul>
                    {boards.map((item) => (
                        <li key={item.id}>
                            <NavLink to={boardRoute(workspace.id, item.id)}>{item.name}</NavLink>
                        </li>
                    ))}
                </ul>
            </nav>
            <h1>{board.name}</h1>
            <TasksSection key={`tasks-${board.id}`} workspace={workspace} board={board} boards={boards} onStale={refresh} />

            <RenameBoardForm
                key={board.id}
                workspaceId={workspace.id}
                board={board}
                etag={etag}
                onStale={refresh}
                onRenamed={(updated) =>
                    setView({
                        kind: 'ready',
                        workspace,
                        board: updated.board,
                        etag: updated.etag,
                        boards: boards.map((item) => (item.id === updated.board.id ? updated.board : item)),
                    })
                }
            />
            {isOwner ? (
                <DeleteBoardPanel
                    key={`delete-${board.id}`}
                    workspaceId={workspace.id}
                    board={board}
                    etag={etag}
                    onStale={refresh}
                    onDeleted={() =>
                        navigate(`/workspaces/${encodeURIComponent(workspace.id)}`, {
                            state: { notice: `Board “${board.name}” deleted.` },
                        })
                    }
                />
            ) : (
                <p>Only the workspace owner can delete a board.</p>
            )}
        </>
    );
}
