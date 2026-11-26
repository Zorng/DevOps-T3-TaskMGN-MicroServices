import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { ApiError, isNotFound } from '../api/errors';
import { describeError } from '../api/messages';
import { isAmbiguousOutcome } from '../api/mutations';
import { usePageLoader } from '../api/usePageLoader';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import {
    MY_INVITATIONS_PATH,
    respondToInvitation,
    type Invitation,
    type InvitationAction,
    type InvitationStatus,
} from './invitationsApi';

const FILTERS = [
    { value: 'pending', label: 'Pending' },
    { value: 'accepted', label: 'Accepted' },
    { value: 'declined', label: 'Declined' },
    { value: 'cancelled', label: 'Cancelled' },
    { value: 'all', label: 'All' },
] as const;
type Filter = (typeof FILTERS)[number]['value'];

const STATUS_LABEL: Record<InvitationStatus, string> = {
    pending: 'Pending',
    accepted: 'Accepted',
    declined: 'Declined',
    cancelled: 'Cancelled',
};

const LATEST = ' The latest list is shown.';

interface Notice {
    kind: 'ok' | 'error';
    text: string;
    /** Set after a successful accept so the person can go straight to the workspace. */
    workspaceId?: string;
}

/** Wording for a failed accept or decline, chosen by error code. Never uses the server's own text. */
function failureText(error: unknown, action: InvitationAction): string {
    if (error instanceof ApiError) {
        if (error.code === 'MEMBER_LIMIT_REACHED') {
            return 'This workspace already has the maximum of 20 members, so you cannot join yet. Your invitation is still pending. Try again after a member leaves or is removed.';
        }
        if (error.code === 'INVITATION_NOT_PENDING') {
            return 'This invitation was already accepted, declined or cancelled, so it cannot be changed.';
        }
        if (isNotFound(error)) {
            return 'This invitation is no longer available. The workspace may have been deleted.';
        }
    }
    const fallback = action === 'accept' ? 'The invitation could not be accepted.' : 'The invitation could not be declined.';
    const text = describeError(error, fallback);
    // Accept and decline are safe to repeat, so an unclear result can simply be tried again.
    return isAmbiguousOutcome(error) ? `${text} It is safe to try again.` : text;
}

export function MyInvitationsPage() {
    const { client } = useAuth();
    const [filter, setFilter] = useState<Filter>('pending');
    // A new filter means a new loader: a cursor only works with the filter that issued it.
    const { items, status, error, hasMore, loadMore, reload } = usePageLoader<Invitation>(client, {
        path: MY_INVITATIONS_PATH,
        query: { status: filter === 'all' ? undefined : filter },
    });

    const [notice, setNotice] = useState<Notice | null>(null);
    const [confirmingDecline, setConfirmingDecline] = useState<string | null>(null); // invitation id
    const [respondingId, setRespondingId] = useState<string | null>(null);
    const listRef = useRef<HTMLUListElement>(null);
    const previousConfirming = useRef<string | null>(null);

    // When the confirmation closes, put keyboard focus back on the button that opened it.
    useEffect(() => {
        if (confirmingDecline === null && previousConfirming.current !== null) {
            listRef.current
                ?.querySelector<HTMLElement>(`[data-decline-for="${previousConfirming.current}"]`)
                ?.focus();
        }
        previousConfirming.current = confirmingDecline;
    }, [confirmingDecline]);

    async function respond(invitation: Invitation, action: InvitationAction) {
        if (respondingId) return;
        setRespondingId(invitation.id);
        setNotice(null);
        try {
            await respondToInvitation(client, invitation.id, action);
            setConfirmingDecline(null);
            setNotice(
                action === 'accept'
                    ? { kind: 'ok', text: `You joined “${invitation.workspace_name}”.`, workspaceId: invitation.workspace_id }
                    : { kind: 'ok', text: `You declined the invitation to “${invitation.workspace_name}”.` },
            );
            reload();
        } catch (caught) {
            setConfirmingDecline(null);
            const text = failureText(caught, action);
            // Anything that may have changed the invitation: show what the server says now.
            const reconcile =
                isNotFound(caught) ||
                isAmbiguousOutcome(caught) ||
                (caught instanceof ApiError && caught.code === 'INVITATION_NOT_PENDING');
            if (reconcile) reload();
            setNotice({ kind: 'error', text: reconcile ? `${text}${LATEST}` : text });
        } finally {
            setRespondingId(null);
        }
    }

    const filterLabel = FILTERS.find((option) => option.value === filter)?.label.toLowerCase() ?? filter;
    const busy = respondingId !== null;

    return (
        <>
            <h1>Invitations</h1>
            <p>
                Invitations to join workspaces, sent to the email address you signed in with. Accepting adds you
                as a member. A workspace can have at most 20 members.
            </p>

            {notice && (
                <p role={notice.kind === 'error' ? 'alert' : 'status'}>
                    {notice.text}
                    {notice.workspaceId && (
                        <>
                            {' '}
                            <Link to={`/workspaces/${notice.workspaceId}`}>Open workspace</Link>
                        </>
                    )}
                </p>
            )}

            <label htmlFor="my-invitation-filter">Show</label>
            <select id="my-invitation-filter" value={filter} onChange={(event) => setFilter(event.target.value as Filter)}>
                {FILTERS.map((option) => (
                    <option key={option.value} value={option.value}>
                        {option.label}
                    </option>
                ))}
            </select>

            {status === 'loading' && items.length === 0 && <LoadingState label="Loading invitations…" />}
            {status === 'error' && items.length === 0 && error && (
                <ErrorState message={describeError(error)} onRetry={loadMore} />
            )}
            {status === 'ready' && items.length === 0 && (
                <EmptyState title="No invitations" message={`You have no ${filterLabel} invitations.`} />
            )}

            {items.length > 0 && (
                <ul ref={listRef} aria-label="My invitations">
                    {items.map((invitation) => {
                        const name = invitation.workspace_name;
                        return (
                            <li key={invitation.id}>
                                <strong>{name}</strong>
                                {' · '}
                                <span>{`Invited by ${invitation.inviter.display_name}`}</span>
                                {' · '}
                                <span>{STATUS_LABEL[invitation.status] ?? invitation.status}</span>
                                {invitation.status === 'pending' && confirmingDecline !== invitation.id && (
                                    <>
                                        {' '}
                                        <button
                                            type="button"
                                            aria-label={`Accept invitation to ${name}`}
                                            disabled={busy}
                                            onClick={() => void respond(invitation, 'accept')}
                                        >
                                            Accept
                                        </button>
                                        <button
                                            type="button"
                                            data-decline-for={invitation.id}
                                            aria-label={`Decline invitation to ${name}`}
                                            disabled={busy}
                                            onClick={() => setConfirmingDecline(invitation.id)}
                                        >
                                            Decline
                                        </button>
                                    </>
                                )}
                                {confirmingDecline === invitation.id && (
                                    <div role="group" aria-label={`Confirm declining the invitation to ${name}`}>
                                        <p>{`Decline the invitation to “${name}”? You will need a new invitation to join later.`}</p>
                                        <button type="button" autoFocus disabled={busy} onClick={() => setConfirmingDecline(null)}>
                                            Keep invitation
                                        </button>
                                        <button type="button" disabled={busy} onClick={() => void respond(invitation, 'decline')}>
                                            Yes, decline invitation
                                        </button>
                                    </div>
                                )}
                            </li>
                        );
                    })}
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
