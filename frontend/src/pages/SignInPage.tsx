import { useEffect, useRef, useState } from 'react';
import { Navigate, useLocation } from 'react-router';
import { describeError } from '../api/messages';
import { useAuth } from '../auth/AuthProvider';
import type { Challenge } from '../auth/authApi';
import type { CredentialResponse, GoogleButtonOptions } from '../auth/gis';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';

type Phase =
    | { kind: 'loading' }
    | { kind: 'ready' }
    | { kind: 'signing_in' }
    | { kind: 'error'; message: string; canRetry: boolean };

const NOT_CONFIGURED = 'Google sign-in is not configured. Set VITE_GOOGLE_CLIENT_ID and restart the app.';
const GOOGLE_LOAD_FAILED = 'Google sign-in could not be loaded. Check your connection and try again.';
const NO_CREDENTIAL = 'Google did not return a sign-in credential. Please try again.';
const SIGN_IN_FAILED = 'Sign-in failed. Please try again.';

const BUTTON_OPTIONS: GoogleButtonOptions = {
    type: 'standard',
    theme: 'outline',
    size: 'large',
    text: 'signin_with',
    shape: 'rectangular',
    width: 280,
};

// A challenge lives five minutes. Refresh a little before it expires, but never in a tight loop
// (a wrong clock on the user's computer must not cause endless refreshes).
const REFRESH_MARGIN_MS = 15_000;
const MIN_REFRESH_MS = 30_000;

export function SignInPage() {
    const { config, service, loadGoogle, snapshot } = useAuth();
    const location = useLocation();
    const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/';
    const buttonRef = useRef<HTMLDivElement>(null);
    const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
    const [attempt, setAttempt] = useState(0); // bump to start over with a fresh challenge

    useEffect(() => {
        if (config.googleClientId === '') {
        setPhase({ kind: 'error', message: NOT_CONFIGURED, canRetry: false });
        return;
        }

        let cancelled = false;
        let refreshTimer: ReturnType<typeof setTimeout> | undefined;
        setPhase({ kind: 'loading' });

        const onCredential = async (challenge: Challenge, response: CredentialResponse) => {
        if (cancelled) return;
        if (!response.credential) {
            setPhase({ kind: 'error', message: NO_CREDENTIAL, canRetry: true });
            return;
        }
        setPhase({ kind: 'signing_in' });
        try {
            // On success the session store changes and this page redirects (see below).
            await service.completeSignIn(challenge, response.credential);
        } catch (error) {
            if (!cancelled) {
            setPhase({ kind: 'error', message: describeError(error, SIGN_IN_FAILED), canRetry: true });
            }
        }
        };

        const prepare = async () => {
        try {
            const [google, challenge] = await Promise.all([loadGoogle(), service.startSignIn()]);
            if (cancelled) return;
            google.initialize({
            client_id: config.googleClientId,
            nonce: challenge.nonce,
            auto_select: false,
            callback: (response) => {
                void onCredential(challenge, response);
            },
            });
            const container = buttonRef.current;
            if (container) {
            container.replaceChildren();
            google.renderButton(container, BUTTON_OPTIONS);
            }
            setPhase({ kind: 'ready' });

            const untilExpiry = Date.parse(challenge.expires_at) - Date.now() - REFRESH_MARGIN_MS;
            refreshTimer = setTimeout(() => setAttempt((n) => n + 1), Math.max(untilExpiry, MIN_REFRESH_MS));
        } catch (error) {
            if (!cancelled) {
            setPhase({ kind: 'error', message: describeError(error, GOOGLE_LOAD_FAILED), canRetry: true });
            }
        }
        };

        void prepare();
        return () => {
        cancelled = true;
        if (refreshTimer !== undefined) clearTimeout(refreshTimer);
        };
    }, [attempt, config.googleClientId, loadGoogle, service]);

    if (snapshot.status === 'signed_in') return <Navigate to={from} replace />;

    return (
        <>
            <h1>Sign in</h1>
            {phase.kind === 'loading' && <LoadingState label="Preparing Google sign-in…" />}
            {phase.kind === 'signing_in' && <LoadingState label="Signing you in…" />}
            {phase.kind === 'error' && (
                <ErrorState
                    title="Sign-in problem"
                    message={phase.message}
                    onRetry={phase.canRetry ? () => setAttempt((n) => n + 1) : undefined}
                />
            )}
            {/* Google draws its button here. It stays mounted so the ref exists when the script is ready. */}
            <div ref={buttonRef} hidden={phase.kind !== 'ready'} />
        </>
    );
}