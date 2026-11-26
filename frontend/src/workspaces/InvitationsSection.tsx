import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, isNotFound } from '../api/errors';
import { describeError, fieldReason } from '../api/messages';
import { isAmbiguousOutcome } from '../api/mutations';
import { usePageLoader } from '../api/usePageLoader';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import {
    cancelInvitation,
    checkInviteEmail,
    createInvitationAttempt,
    invitationsPath,
    type Invitation,
    type InvitationStatus,
} from './invitationsApi';
import { WORKSPACE_MEMBER_LIMIT, type Workspace } from './workspacesApi';

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

interface PendingInvite {
    email: string;
    create: ReturnType<typeof createInvitationAttempt>;
}

interface InvitationsSectionProps {
    workspace: Workspace;
    /** Re-read the workspace, for example after a 404 that may mean it was deleted. */
    onChanged: () => Promise<void>;
    /** The workspace is no longer accessible. */
    onGone: () => void;
}

function inviteMessage(error: unknown, canRetry: boolean): string {
    const text = fieldReason(error, 'email') ?? describeError(error, 'The invitation could not be sent.');
    return canRetry ? `${text} Press “Send invitation” again to retry. It will not send a duplicate.` : text;
}

export function InvitationsSection({ workspace, onChanged, onGone }: InvitationsSectionProps) {
    const { client } = useAuth();
    const [filter, setFilter] = useState<Filter>('pending');
    // A new filter means a new loader: a cursor only works with the filter that issued it.
    const { items, status, error, hasMore, loadMore, reload } = usePageLoader<Invitation>(client, {
        path: invitationsPath(workspace.id),
        query: { status: filter === 'all' ? undefined : filter },
    });

    const [email, setEmail] = useState('');
    const [formError, setFormError] = useState<string | null>(null);
    const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
    const [sending, setSending] = useState(false);
    const [confirming, setConfirming] = useState<string | null>(null); // invitation id
    const [cancellingId, setCancellingId] = useState<string | null>(null);
    const pending = useRef<PendingInvite | null>(null);
    const listRef = useRef<HTMLUListElement>(null);
    const previousConfirming = useRef<string | null>(null);

    useEffect(() => {
        if (error && isNotFound(error)) onGone();
    }, [error, onGone]);

    useEffect(() => {
        if (confirming === null && previousConfirming.current !== null) {
            listRef.current
                ?.querySelector<HTMLElement>(`[data-cancel-for="${previousConfirming.current}"]`)
                ?.focus();
        }
        previousConfirming.current = confirming;
    }, [confirming]);

    async function handleInvite(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (sending) return;
        const check = checkInviteEmail(email);
        if (!check.ok) {
            setFormError(check.message);
            return;
        }
        setFormError(null);
        setNotice(null);

        let current = pending.current;
        if (!current || current.email !== check.email || current.create.attempt.state === 'rejected') {
            current = { email: check.email, create: createInvitationAttempt(client, workspace.id, check.email) };
            pending.current = current;
        }

        setSending(true);
        try {
            const invitation = await current.create.send();
            pending.current = null;
            setEmail('');
            setNotice({ kind: 'ok', text: `Invitation sent to ${invitation.recipient.display_name}.` });
            reload();
        } catch (caught) {
        if (isNotFound(caught)) {
            // USER_NOT_FOUND is a different code. A plain NOT_FOUND means the workspace itself is gone.
            setFormError(describeError(caught));
            await onChanged();
        } else {
            setFormError(inviteMessage(caught, current.create.attempt.state === 'retryable'));
        }
        } finally {
            setSending(false);
        }
    }

    async function cancel(invitation: Invitation) {
        if (cancellingId) return;
            const name = invitation.recipient.display_name;
            setCancellingId(invitation.id);
            setNotice(null);
        try {
            await cancelInvitation(client, invitation.id);
            setConfirming(null);
            setNotice({ kind: 'ok', text: `The invitation for ${name} was cancelled.` });
            reload();
        } catch (caught) {
            setConfirming(null);
            if (isNotFound(caught)) {
                setNotice({ kind: 'error', text: 'This invitation or workspace is no longer available.' });
                reload();
                await onChanged();
            } else {
                // An invitation that already reached a final state, or an unclear result: show the real state.
                const reconcile =
                isAmbiguousOutcome(caught) || (caught instanceof ApiError && caught.code === 'INVITATION_NOT_PENDING');
                if (reconcile) reload();
                const text = describeError(caught, 'The invitation could not be cancelled.');
                setNotice({ kind: 'error', text: reconcile ? `${text} The latest list is shown.` : text });
            }
        } finally {
            setCancellingId(null);
        }
    }

    const full = workspace.member_count >= WORKSPACE_MEMBER_LIMIT;
    const filterLabel = FILTERS.find((option) => option.value === filter)?.label.toLowerCase() ?? filter;

    return (
        <section aria-labelledby="invitations-title">
            <h2 id="invitations-title">Invitations</h2>
            <p>
                Invite someone by the exact email address they signed in with. They must have signed in to this
                app at least once. Pending invitations do not reserve member slots.
            </p>
            {full && (
                <p>
                    This workspace has the maximum of {WORKSPACE_MEMBER_LIMIT} members. Invitations cannot be
                    accepted until a member leaves or is removed.
                </p>
            )}

            <form onSubmit={handleInvite} noValidate aria-label="Invite someone">
                <label htmlFor="invite-email">Email address</label>
                <input
                    id="invite-email"
                    type="text"
                    inputMode="email"
                    autoComplete="off"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    aria-invalid={formError !== null}
                    aria-describedby={formError ? 'invite-error' : undefined}
                />
                <button type="submit" disabled={sending}>
                    Send invitation
                </button>
                {formError && (
                    <p id="invite-error" role="alert">
                        {formError}
                    </p>
                )}
            </form>
            {notice && <p role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</p>}

            <label htmlFor="invitation-filter">Show</label>
            <select
                id="invitation-filter"
                value={filter}
                onChange={(event) => setFilter(event.target.value as Filter)}
            >
                {FILTERS.map((option) => (
                    <option key={option.value} value={option.value}>
                        {option.label}
                    </option>
                ))}
            </select>

            {status === 'loading' && items.length === 0 && <LoadingState label="Loading invitations…" />}
            {status === 'error' && items.length === 0 && error && !isNotFound(error) && (
                <ErrorState message={describeError(error)} onRetry={loadMore} />
            )}
            {status === 'ready' && items.length === 0 && (
                <EmptyState title="No invitations" message={`There are no ${filterLabel} invitations for this workspace.`} />
            )}

            {items.length > 0 && (
                <ul ref={listRef} aria-label="Invitations">
                    {items.map((invitation) => {
                        const name = invitation.recipient.display_name;
                        return (
                            <li key={invitation.id}>
                                <span>{name}</span>
                                {' · '}
                                <span>{STATUS_LABEL[invitation.status] ?? invitation.status}</span>
                                {invitation.status === 'pending' && confirming !== invitation.id && (
                                    <button
                                        type="button"
                                        data-cancel-for={invitation.id}
                                        aria-label={`Cancel invitation for ${name}`}
                                        disabled={cancellingId !== null}
                                        onClick={() => setConfirming(invitation.id)}
                                    >
                                        Cancel
                                    </button>
                                )}
                                {confirming === invitation.id && (
                                    <div role="group" aria-label={`Confirm cancelling the invitation for ${name}`}>
                                        <p>{`Cancel the invitation for ${name}? They will no longer be able to accept it.`}</p>
                                        <button type="button" autoFocus disabled={cancellingId !== null} onClick={() => setConfirming(null)}>
                                            Keep invitation
                                        </button>
                                        <button type="button" disabled={cancellingId !== null} onClick={() => void cancel(invitation)}>
                                            Yes, cancel invitation
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
        </section>
    );
}