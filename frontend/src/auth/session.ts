export interface User{
    id: string,
    display_name: string,
    email: string,
    created_at: string
}

export interface Session {
    access_token: string;
    token_type: 'Bearer';
    expires_at: string;
    user: User;
}

export type SessionStatus = 'signed_out' | 'signed_in';

/** What the UI may see. The token is deliberately not part of it. */
export interface SessionSnapshot {
    readonly status: SessionStatus;
    readonly user: User | null;
    readonly expiresAt: string | null;
}

const SIGNED_OUT: SessionSnapshot = Object.freeze({
    status: 'signed_out',
    user: null,
    expiresAt: null,
});

/**
 * Holds the application session in memory only. The token is never written to
 * localStorage, sessionStorage, cookies, or the snapshot the UI reads, so a page reload
 * means a new sign-in (api_contracts/authentication.md).
 */
export class SessionStore {
    private token: string | null = null;
    private expiresAtMs: number | null = null;
    private timer: ReturnType<typeof setTimeout> | undefined;
    private snapshot: SessionSnapshot = SIGNED_OUT;
    private readonly listeners = new Set<() => void>();

    /** Arrow properties so they can be passed straight to React's useSyncExternalStore. */
    getSnapshot = (): SessionSnapshot => this.snapshot;

    subscribe = (listener: () => void): (() => void) => {
        this.listeners.add(listener);
        return () => {
        this.listeners.delete(listener);
        };
    };

    /** For the API client's getToken. Returns null (and signs out) once the session has expired. */
    getToken(): string | null {
        if (this.token !== null && this.expiresAtMs !== null && Date.now() >= this.expiresAtMs) {
        this.signOut();
        return null;
        }
        return this.token;
    }

    signIn(session: Pick<Session, 'access_token' | 'expires_at' | 'user'>): void {
        const expiresAtMs = Date.parse(session.expires_at);
        if (session.access_token === '' || Number.isNaN(expiresAtMs) || expiresAtMs <= Date.now()) {
        throw new Error('The sign-in response did not contain a usable session.');
        }
        this.clearTimer();
        this.token = session.access_token;
        this.expiresAtMs = expiresAtMs;
        this.snapshot = Object.freeze({
        status: 'signed_in',
        user: session.user,
        expiresAt: session.expires_at,
        });
        this.timer = setTimeout(() => this.signOut(), expiresAtMs - Date.now());
        this.notify();
    }

    /** Clears everything. Safe to call repeatedly (e.g. several requests failing with 401 at once). */
    signOut(): void {
        this.clearTimer();
        this.token = null;
        this.expiresAtMs = null;
        if (this.snapshot === SIGNED_OUT) return;
        this.snapshot = SIGNED_OUT;
        this.notify();
    }

    /** Refreshes the displayed profile after GET /me. Ignored while signed out. */
    setUser(user: User): void {
        if (this.snapshot.status !== 'signed_in') return;
        this.snapshot = Object.freeze({ ...this.snapshot, user });
        this.notify();
    }

    private clearTimer(): void {
        if (this.timer !== undefined) {
        clearTimeout(this.timer);
        this.timer = undefined;
        }
    }

    private notify(): void {
        for (const listener of [...this.listeners]) listener();
    }
}
