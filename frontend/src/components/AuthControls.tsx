import { useState } from 'react';
import { Link } from 'react-router';
import { useAuth } from '../auth/AuthProvider';

const NOT_CONFIRMED_NOTICE =
    'You were signed out on this device, but the server could not confirm it. Your session ends automatically within 8 hours.';

export function AuthControls() {
    const { snapshot, service, loadGoogle } = useAuth();
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);

    async function handleSignOut() {
        setBusy(true);
        setNotice(null);
        const outcome = await service.signOut();
        // Stop Google from silently re-selecting the account on the next visit (the script is already loaded).
        loadGoogle()
        .then((google) => google.disableAutoSelect())
        .catch(() => undefined);
        setBusy(false);
        if (outcome === 'not_confirmed') setNotice(NOT_CONFIRMED_NOTICE);
    }

    return (
        <div>
            {snapshot.status === 'signed_in' ? (
                <>
                    <span>{snapshot.user?.display_name}</span>
                    <button type="button" onClick={() => void handleSignOut()} disabled={busy}>
                        Sign out
                    </button>
                </>
            ) : (
                <Link to="/sign-in">Sign in</Link>
            )}
            {notice && <p role="status">{notice}</p>}
        </div>
    );
}