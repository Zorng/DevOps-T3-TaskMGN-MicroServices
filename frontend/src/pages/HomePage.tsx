import { useEffect, useState } from 'react';
import { describeError } from '../api/messages';
import { useAuth } from '../auth/AuthProvider';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';

type ProfileState =
    | { kind: 'loading' }
    | { kind: 'ready'; ownedWorkspaces: number }
    | { kind: 'error'; message: string };

export function HomePage() {
    const { snapshot, service } = useAuth();
    const [profile, setProfile] = useState<ProfileState>({ kind: 'loading' });
    const [reloads, setReloads] = useState(0);

    useEffect(() => {
        let cancelled = false;
        setProfile({ kind: 'loading' });
        service.loadMe().then(
            (me) => {
                if (!cancelled) setProfile({ kind: 'ready', ownedWorkspaces: me.owned_workspace_count });
            },
            (error: unknown) => {
                if (!cancelled) setProfile({ kind: 'error', message: describeError(error) });
            },
        );
        return () => {
            cancelled = true;
        };
    }, [service, reloads]);

    return (
        <>
            <h1>Task Management</h1>
            <p>Signed in as {snapshot.user?.display_name}.</p>
            {profile.kind === 'loading' && <LoadingState label="Loading your profile…" />}
            {profile.kind === 'ready' && <p>You own {profile.ownedWorkspaces} of 10 workspaces.</p>}
            {profile.kind === 'error' && (
                <ErrorState message={profile.message} onRetry={() => setReloads((n) => n + 1)} />
            )}
        </>
    );
}