import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import type { ApiClient } from '../api/client';
import { isNotFound } from '../api/errors';
import { describeError, fieldReason } from '../api/messages';
import { isAmbiguousOutcome, isVersionConflict } from '../api/mutations';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { MembersSection } from './MembersSection';
import {
    checkWorkspaceName,
    deleteWorkspace,
    getWorkspace,
    OWNED_WORKSPACE_LIMIT,
    renameWorkspace,
    WORKSPACE_MEMBER_LIMIT,
    type VersionedWorkspace,
    type Workspace,
} from './workspacesApi';

type View =
    | { kind: 'loading' }
    | { kind: 'ready'; workspace: Workspace; etag: string }
    | { kind: 'unavailable' }
    | { kind: 'error'; message: string };

const LATEST_SHOWN = ' The latest version of the workspace is shown.';

async function loadView(client: ApiClient, id: string, signal?: AbortSignal): Promise<View> {
    try {
        const { workspace, etag } = await getWorkspace(client, id, signal);
        return { kind: 'ready', workspace, etag };
    } catch (error) {
        if (isNotFound(error)) return { kind: 'unavailable' };
        return { kind: 'error', message: describeError(error) };
    }
}

interface OwnerControlProps {
    workspace: Workspace;
    etag: string;
    /** Reload the latest version after a conflict or an unclear result. */
    onStale: () => Promise<void>;
    /** The workspace no longer exists or is no longer accessible. */
    onGone: () => void;
}

function RenameForm({
    workspace,
    etag,
    onStale,
    onGone,
    onRenamed,
}: OwnerControlProps & { onRenamed: (updated: VersionedWorkspace) => void }) {
    const { client } = useAuth();
    const [name, setName] = useState(workspace.name);
    const [message, setMessage] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null);
    const [busy, setBusy] = useState(false);

    async function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (busy) return;
        const check = checkWorkspaceName(name);
        if (!check.ok) {
            setMessage({ kind: 'error', text: check.message });
            return;
        }
        if (check.name === workspace.name) {
            setMessage({ kind: 'error', text: 'Enter a different name to rename the workspace.' });
            return;
        }
            setBusy(true);
            setMessage(null);
        try {
            const updated = await renameWorkspace(client, workspace.id, check.name, etag);
            setName(updated.workspace.name);
            setMessage({ kind: 'ok', text: 'Workspace renamed.' });
            onRenamed(updated);
        } catch (error) {
            if (isNotFound(error)) {
                onGone();
                return;
            }
            // After a stale version or an unclear result, read the server's state instead of re-sending.
                const reconcile = isVersionConflict(error) || isAmbiguousOutcome(error);
            if (reconcile) await onStale();
                const text = fieldReason(error, 'name') ?? describeError(error, 'The workspace could not be renamed.');
                setMessage({ kind: 'error', text: reconcile ? `${text}${LATEST_SHOWN}` : text });
        } finally {
            setBusy(false);
        }
    }

    return (
        <form onSubmit={handleSubmit} noValidate aria-label="Rename workspace">
            <label htmlFor="rename-workspace">Workspace name</label>
            <input
                id="rename-workspace"
                value={name}
                onChange={(event) => setName(event.target.value)}
                aria-invalid={message?.kind === 'error'}
                aria-describedby={message ? 'rename-message' : undefined}
            />
            <button type="submit" disabled={busy}>
                Rename workspace
            </button>
            {message && (
                <p id="rename-message" role={message.kind === 'error' ? 'alert' : 'status'}>
                    {message.text}
                </p>
            )}
        </form>
    );
}

function DeletePanel({
    workspace,
    etag,
    onStale,
    onGone,
    onDeleted,
}: OwnerControlProps & { onDeleted: () => void }) {
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
        await deleteWorkspace(client, workspace.id, etag);
        onDeleted();
        } catch (caught) {
        if (isNotFound(caught)) {
            onGone();
            return;
        }
        const reconcile = isVersionConflict(caught) || isAmbiguousOutcome(caught);
        if (reconcile) await onStale();
        const text = describeError(caught, 'The workspace could not be deleted.');
        setError(reconcile ? `${text}${LATEST_SHOWN} Confirm again if you still want to delete it.` : text);
        } finally {
        setBusy(false);
        }
    }

    if (!confirming) {
        return (
        <button type="button" ref={openButton} onClick={() => setConfirming(true)}>
            Delete workspace
        </button>
        );
    }

    return (
        <section aria-labelledby="delete-title">
            <h2 id="delete-title">Delete “{workspace.name}”?</h2>
            <p>
                This permanently deletes the workspace and everything in it: its boards, tasks and comments. All
                members lose access and pending invitations are cancelled. This cannot be undone.
            </p>
            <p>Deleting it frees one of your {OWNED_WORKSPACE_LIMIT} owned-workspace slots.</p>
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
            <button type="button" disabled={busy} onClick={confirmDelete}>
                Yes, delete workspace
            </button>
        </section>
    );
}

export function WorkspacePage() {
    const { workspaceId } = useParams();
    const { client } = useAuth();
    const navigate = useNavigate();
    const [view, setView] = useState<View>({ kind: 'loading' });
    const [reloads, setReloads] = useState(0);

    useEffect(() => {
        if (!workspaceId) {
            setView({ kind: 'unavailable' });
            return;
        }
        const controller = new AbortController();
        setView({ kind: 'loading' });
        void loadView(client, workspaceId, controller.signal).then((next) => {
            if (!controller.signal.aborted) setView(next);
        });
        return () => controller.abort();
    }, [client, workspaceId, reloads]);

  /** Re-read the workspace after a conflict. A failed re-read keeps what is on screen. */
    const refresh = useCallback(async () => {
        if (!workspaceId) return;
        const next = await loadView(client, workspaceId);
        if (next.kind === 'ready' || next.kind === 'unavailable') setView(next);
    }, [client, workspaceId]);

    const gone = useCallback(() => setView({ kind: 'unavailable' }), []);

    if (view.kind === 'loading') return <LoadingState label="Loading workspace…" />;
    if (view.kind === 'error') {
        return <ErrorState message={view.message} onRetry={() => setReloads((n) => n + 1)} />;
    }
    if (view.kind === 'unavailable') {
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

    const { workspace, etag } = view;
    const isOwner = workspace.my_role === 'owner';

    return (
        <>
            <p>
                <Link to="/workspaces">Back to workspaces</Link>
            </p>
            <h1>{workspace.name}</h1>
            <p>
                {`Your role: ${isOwner ? 'Owner' : 'Member'} · ${workspace.member_count} of ${WORKSPACE_MEMBER_LIMIT} members`}
            </p>

            {isOwner ? (
                <>
                    <RenameForm
                        key={workspace.id}
                        workspace={workspace}
                        etag={etag}
                        onStale={refresh}
                        onGone={gone}
                        onRenamed={(updated) =>
                            setView({ kind: 'ready', workspace: updated.workspace, etag: updated.etag })
                        }
                    />
                    <DeletePanel
                        key={`delete-${workspace.id}`}
                        workspace={workspace}
                        etag={etag}
                        onStale={refresh}
                        onGone={gone}
                        onDeleted={() =>
                            navigate('/workspaces', { state: { notice: `Workspace “${workspace.name}” deleted.` } })
                        }
                    />
                </>
            ) : (
                <p>Only the workspace owner can rename or delete it.</p>
            )}
            <MembersSection
                key={`members-${workspace.id}`}
                workspace={workspace}
                onChanged={refresh}
                onGone={gone}
                onLeft={() =>
                navigate('/workspaces', { state: { notice: `You left “${workspace.name}”.` } })
                }
            />
        </>
    );
}