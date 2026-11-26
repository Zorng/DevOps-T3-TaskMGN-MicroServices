import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useLocation } from 'react-router';
import { ApiError } from '../api/errors';
import { describeError } from '../api/messages';
import { usePageLoader } from '../api/usePageLoader';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import {
    checkWorkspaceName,
    createWorkspaceAttempt,
    OWNED_WORKSPACE_LIMIT,
    WORKSPACE_MEMBER_LIMIT,
    type Workspace,
} from './workspacesApi';

interface PendingCreate {
    name: string;
    create: ReturnType<typeof createWorkspaceAttempt>;
}

function formMessage(error: unknown, canRetry: boolean): string {
    if (error instanceof ApiError && error.code === 'VALIDATION_FAILED') {
        const nameDetail = error.details.find((detail) => detail.field === 'name');
        if (nameDetail) return nameDetail.reason;
    }
    const text = describeError(error, 'The workspace could not be created.');
    return canRetry
        ? `${text} Press “Create workspace” again to retry. It will not create a duplicate.`
        : text;
}

function limitMessage(owned: number): string {
    const joinedNote = 'Workspaces you have joined do not count toward this limit.';
    if (owned >= OWNED_WORKSPACE_LIMIT) {
        return `You own ${owned} of ${OWNED_WORKSPACE_LIMIT} workspaces, the maximum. Delete one of your workspaces to create another. ${joinedNote}`;
    }
    return `You own ${owned} of ${OWNED_WORKSPACE_LIMIT} workspaces. ${joinedNote}`;
}

function noticeFromState(state: unknown): string | null {
    if (typeof state === 'object' && state !== null && 'notice' in state && typeof state.notice === 'string') {
        return state.notice;
    }
    return null;
}

export function WorkspacesPage() {
    const location = useLocation();
    const { client, service } = useAuth();
    const { items, status, error, hasMore, loadMore, reload } = usePageLoader<Workspace>(client, {
        path: '/workspaces',
    });

    const [ownedCount, setOwnedCount] = useState<number | null>(null);
    const [name, setName] = useState('');
    const [formError, setFormError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(() => noticeFromState(location.state));
    const [submitting, setSubmitting] = useState(false);
    // Keeps the same attempt (and Idempotency-Key) across retries of the same name.
    const pending = useRef<PendingCreate | null>(null);

    const refreshCount = useCallback(() => {
        service.loadMe().then(
        (me) => setOwnedCount(me.owned_workspace_count),
        () => undefined, // an unknown count just hides the explanation; the server still enforces the limit
        );
    }, [service]);

    useEffect(() => {
        refreshCount();
    }, [refreshCount]);

    const atLimit = ownedCount !== null && ownedCount >= OWNED_WORKSPACE_LIMIT;

    async function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (submitting) return;

        const check = checkWorkspaceName(name);
        if (!check.ok) {
        setFormError(check.message);
        return;
        }
        setFormError(null);
        setNotice(null);

        let current = pending.current;
        if (!current || current.name !== check.name || current.create.attempt.state === 'rejected') {
        current = { name: check.name, create: createWorkspaceAttempt(client, check.name) };
        pending.current = current;
        }

        setSubmitting(true);
        try {
        const created = await current.create.send();
        pending.current = null;
        setName('');
        setNotice(`Workspace “${created.workspace.name}” created.`);
        reload();
        refreshCount();
        } catch (caught) {
        setFormError(formMessage(caught, current.create.attempt.state === 'retryable'));
        } finally {
        setSubmitting(false);
        }
    }

    return (
        <>
            <h1>Workspaces</h1>
            {ownedCount !== null && <p>{limitMessage(ownedCount)}</p>}

            <form onSubmit={handleSubmit} noValidate>
                <label htmlFor="workspace-name">Workspace name</label>
                <input
                    id="workspace-name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    aria-invalid={formError !== null}
                    aria-describedby={formError ? 'workspace-form-error' : undefined}
                />
                <button type="submit" disabled={atLimit || submitting}>
                    Create workspace
                </button>
                {formError && (
                    <p id="workspace-form-error" role="alert">
                        {formError}
                    </p>
                )}
            </form>
            {notice && <p role="status">{notice}</p>}

            {status === 'loading' && items.length === 0 && <LoadingState label="Loading workspaces…" />}
            {status === 'error' && items.length === 0 && error && (
                <ErrorState message={describeError(error)} onRetry={loadMore} />
            )}
            {status === 'ready' && items.length === 0 && (
                <EmptyState title="No workspaces yet" message="Create your first workspace with the form above." />
            )}

            {items.length > 0 && (
                <ul>
                    {items.map((workspace) => (
                        <li key={workspace.id}>
                            <Link to={`/workspaces/${workspace.id}`}>{workspace.name}</Link>{' '}
                            <span>{workspace.my_role === 'owner' ? 'Owner' : 'Member'}</span>
                            {' · '}
                            <span>{`${workspace.member_count} of ${WORKSPACE_MEMBER_LIMIT} members`}</span>
                        </li>
                    ))}
                </ul>
            )}
            {items.length > 0 && status === 'loading' && <LoadingState label="Loading more…" />}
            {items.length > 0 && status === 'error' && error && (
                <ErrorState message={describeError(error)} onRetry={loadMore} retryLabel="Try loading more again" />
            )}
            {status === 'ready' && hasMore && (
                <button type="button" onClick={loadMore}>
                    Load more
                </button>
            )}
        </>
    );
}