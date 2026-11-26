import { useEffect, useRef, useState } from 'react';
import { ApiError, isNotFound } from '../api/errors';
import { describeError } from '../api/messages';
import { isAmbiguousOutcome } from '../api/mutations';
import { useAuth } from '../auth/AuthProvider';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import {
    leaveWorkspace,
    listMembers,
    removeMember,
    WORKSPACE_MEMBER_LIMIT,
    type Member,
    type Workspace,
} from './workspacesApi';

const LATEST = ' The latest information is shown.';

interface MembersSectionProps {
    workspace: Workspace;
    /** Re-read the workspace: a membership change also changes its version and member count. */
    onChanged: () => Promise<void>;
    /** The workspace is no longer accessible. */
    onGone: () => void;
    /** The current user left. */
    onLeft: () => void;
}

export function MembersSection({ workspace, onChanged, onGone, onLeft }: MembersSectionProps) {
    const { client, snapshot } = useAuth();
    const myId = snapshot.user?.id;
    const isOwner = workspace.my_role === 'owner';

    const [members, setMembers] = useState<Member[] | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [reloads, setReloads] = useState(0);
    const [removing, setRemoving] = useState<string | null>(null); // user id awaiting confirmation
    const [leaving, setLeaving] = useState(false);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);

    const listRef = useRef<HTMLUListElement>(null);
    const leaveButton = useRef<HTMLButtonElement>(null);
    const previousRemoving = useRef<string | null>(null);
    const wasLeaving = useRef(false);

    useEffect(() => {
        const controller = new AbortController();
        setLoadError(null);
        listMembers(client, workspace.id, controller.signal).then(
            (list) => {
                if (!controller.signal.aborted) setMembers(list);
            },
            (error: unknown) => {
                if (controller.signal.aborted) return;
                if (isNotFound(error)) onGone();
                else setLoadError(describeError(error));
            },
        );
        return () => controller.abort();
    }, [client, workspace.id, reloads, onGone]);

    // When a confirmation closes, put keyboard focus back on the button that opened it.
    useEffect(() => {
        if (removing === null && previousRemoving.current !== null) {
            listRef.current
                ?.querySelector<HTMLElement>(`[data-remove-for="${previousRemoving.current}"]`)
                ?.focus();
        }
        previousRemoving.current = removing;
    }, [removing]);

    useEffect(() => {
        if (!leaving && wasLeaving.current) leaveButton.current?.focus();
        wasLeaving.current = leaving;
    }, [leaving]);

    async function handleFailure(error: unknown, fallback: string, notFoundText: string) {
        setRemoving(null);
        setLeaving(false);
        const notFound = isNotFound(error);
        // After an unclear result or a lost role, read the server's state instead of guessing.
        const reconcile =
        isAmbiguousOutcome(error) || (error instanceof ApiError && error.code === 'FORBIDDEN');
        const text = notFound ? notFoundText : describeError(error, fallback) + (reconcile ? LATEST : '');
        setMessage({ kind: 'error', text });
        if (notFound || reconcile) {
            setReloads((n) => n + 1);
            await onChanged(); // if the workspace itself is gone, the page switches to "not available"
        }
    }

    async function remove(member: Member) {
        if (busy) return;
        setBusy(true);
        setMessage(null);
        try {
            await removeMember(client, workspace.id, member.user.id);
            setRemoving(null);
            setMessage({ kind: 'ok', text: `${member.user.display_name} was removed from the workspace.` });
            setReloads((n) => n + 1);
            await onChanged();
        } catch (error) {
            await handleFailure(error, 'The member could not be removed.', 'That member is no longer in this workspace.');
        } finally {
            setBusy(false);
        }
    }

    async function leave() {
        if (busy) return;
        setBusy(true);
        setMessage(null);
        try {
            await leaveWorkspace(client, workspace.id);
            onLeft();
        } catch (error) {
            await handleFailure(error, 'You could not leave the workspace.', 'You are no longer a member of this workspace.');
        } finally {
            setBusy(false);
        }
    }

    const full = members !== null && members.length >= WORKSPACE_MEMBER_LIMIT;

    return (
        <section aria-labelledby="members-title">
            <h2 id="members-title">Members</h2>

            {members === null && loadError === null && <LoadingState label="Loading members…" />}
            {members === null && loadError !== null && (
                <ErrorState message={loadError} onRetry={() => setReloads((n) => n + 1)} />
            )}

            {members !== null && (
                <ul ref={listRef} aria-label="Workspace members">
                    {members.map((member) => {
                        const name = member.user.display_name;
                        const canRemove = isOwner && member.role !== 'owner';
                        return (
                            <li key={member.membership_id}>
                                <span>{member.user.id === myId ? `${name} (you)` : name}</span>
                                {' · '}
                                <span>{member.role === 'owner' ? 'Owner' : 'Member'}</span>
                                {canRemove && removing !== member.user.id && (
                                    <button
                                        type="button"
                                        data-remove-for={member.user.id}
                                        aria-label={`Remove ${name}`}
                                        disabled={busy}
                                        onClick={() => setRemoving(member.user.id)}
                                    >
                                        Remove
                                    </button>
                                )}
                                {canRemove && removing === member.user.id && (
                                    <div role="group" aria-label={`Confirm removing ${name}`}>
                                        <p>
                                            {`Remove ${name} from this workspace? They lose access immediately and their task assignments here are cleared. Their comments stay.`}
                                        </p>
                                        <button type="button" autoFocus disabled={busy} onClick={() => setRemoving(null)}>
                                            Cancel
                                        </button>
                                        <button type="button" disabled={busy} onClick={() => void remove(member)}>
                                            {`Yes, remove ${name}`}
                                        </button>
                                    </div>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}

            {full && (
                <p>
                    This workspace has the maximum of {WORKSPACE_MEMBER_LIMIT} members. Invitations stay pending and
                    cannot be accepted until someone leaves or is removed. Pending invitations do not reserve slots.
                </p>
            )}

            {message && <p role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>}

            {isOwner ? (
                <p>You are the owner, so you cannot leave this workspace. Delete it instead if you no longer need it.</p>
            ) : leaving ? (
                <div role="group" aria-label="Confirm leaving">
                    <p>
                        {`Leave “${workspace.name}”? You will lose access to its boards and tasks, and your task assignments here are cleared. Your comments stay. You can only rejoin if the owner invites you again.`}
                    </p>
                    <button type="button" autoFocus disabled={busy} onClick={() => setLeaving(false)}>
                        Cancel
                    </button>
                    <button type="button" disabled={busy} onClick={() => void leave()}>
                        Yes, leave workspace
                    </button>
                </div>
            ) : (
                <button type="button" ref={leaveButton} disabled={busy} onClick={() => setLeaving(true)}>
                    Leave workspace
                </button>
            )}
        </section>
    );
}