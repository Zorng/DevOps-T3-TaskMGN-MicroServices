import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { isForbidden, isNotFound } from '../api/errors';
import { describeError } from '../api/messages';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { getInvitation, type Invitation } from './invitationsApi';

type View =
    | { kind: 'loading' }
    | { kind: 'ready'; invitation: Invitation }
    | { kind: 'unavailable' }
    | { kind: 'error'; message: string };

const STATUS_TEXT: Record<Invitation['status'], string> = {
    pending: 'This invitation is waiting for your answer.',
    accepted: 'You accepted this invitation.',
    declined: 'You declined this invitation.',
    cancelled: 'This invitation was cancelled by the workspace owner.',
};

/** One invitation, opened from a notification. The server decides whether it is still visible to this user. */
export function InvitationPage() {
    const { invitationId } = useParams();
    const { client } = useAuth();
    const [view, setView] = useState<View>({ kind: 'loading' });
    const [reloads, setReloads] = useState(0);

    useEffect(() => {
        if (!invitationId) {
            setView({ kind: 'unavailable' });
            return;
        }
        const controller = new AbortController();
        setView({ kind: 'loading' });
        getInvitation(client, invitationId, controller.signal).then(
            (invitation) => {
                if (!controller.signal.aborted) setView({ kind: 'ready', invitation });
            },
            (caught: unknown) => {
                if (controller.signal.aborted) return;
                if (isNotFound(caught) || isForbidden(caught)) setView({ kind: 'unavailable' });
                else setView({ kind: 'error', message: describeError(caught) });
            },
        );
        return () => controller.abort();
    }, [client, invitationId, reloads]);

    if (view.kind === 'loading') return <LoadingState label="Loading invitation…" />;
    if (view.kind === 'error') return <ErrorState message={view.message} onRetry={() => setReloads((n) => n + 1)} />;
    if (view.kind === 'unavailable') {
        return (
            <>
                <EmptyState
                    title="Invitation not available"
                    message="This invitation may have been deleted, or you may no longer have access to it."
                />
                <p>
                    <Link to="/notifications">Back to notifications</Link>
                </p>
            </>
        );
    }

    const { invitation } = view;
    return (
        <>
            <h1>{`Invitation to ${invitation.workspace_name}`}</h1>
            <p>{`Invited by ${invitation.inviter.display_name}`}</p>
            <p>{STATUS_TEXT[invitation.status]}</p>
            {invitation.status === 'pending' && (
                <p>
                    <Link to="/invitations">Go to my invitations to accept or decline</Link>
                </p>
            )}
            {invitation.status === 'accepted' && (
                <p>
                    <Link to={`/workspaces/${encodeURIComponent(invitation.workspace_id)}`}>Open workspace</Link>
                </p>
            )}
            <p>
                <Link to="/notifications">Back to notifications</Link>
            </p>
        </>
    );
}
