import type { ApiClient } from '../api/client';
import { ApiError } from '../api/errors';
import { createChallenge, exchangeCredential, fetchMe, logout, type Challenge, type Me } from './authApi';
import type { SessionStore, User } from './session';

/**
 * 'revoked': the server revoked the session (204), or it was already invalid (401), or there was none.
 * 'not_confirmed': local state was cleared, but the server could not confirm revocation
 * (network error, 5xx). The session may stay valid on the server until it expires.
 */
export type LogoutOutcome = 'revoked' | 'not_confirmed';

/**
 * Sign-in orchestration. The Google popup itself lives in the UI (Step 7): the UI calls
 * startSignIn(), passes the nonce to Google, and gives the returned ID token to completeSignIn().
 * The exchange is never retried: any failure means starting over with a fresh challenge.
 */
export class AuthService {
    private readonly client: ApiClient;
    private readonly store: SessionStore;
    private exchanging: { challengeId: string; promise: Promise<User> } | undefined;

    constructor(client: ApiClient, store: SessionStore) {
        this.client = client;
        this.store = store;
    }

    startSignIn(): Promise<Challenge> {
        return createChallenge(this.client);
    }

    completeSignIn(challenge: Challenge, idToken: string): Promise<User> {
        // Google may invoke its callback twice; the same challenge can only be exchanged once.
        if (this.exchanging?.challengeId === challenge.challenge_id) return this.exchanging.promise;

        if (Date.parse(challenge.expires_at) <= Date.now()) {
        return Promise.reject(
            new ApiError({
            status: 401,
            code: 'AUTH_CHALLENGE_INVALID',
            message: 'The sign-in challenge has expired. Start sign-in again.',
            }),
        );
        }

        const promise = exchangeCredential(this.client, {
        challengeId: challenge.challenge_id,
        idToken,
        })
        .then((session) => {
            this.store.signIn(session);
            return session.user;
        })
        .finally(() => {
            if (this.exchanging?.challengeId === challenge.challenge_id) this.exchanging = undefined;
        });
        this.exchanging = { challengeId: challenge.challenge_id, promise };
        return promise;
    }

    /** Loads the current profile and owned-workspace count, and refreshes the displayed user. */
    async loadMe(): Promise<Me> {
        const me = await fetchMe(this.client);
        this.store.setUser(me.user);
        return me;
    }

    /** Local state is always cleared, even if the server call fails. */
    async signOut(): Promise<LogoutOutcome> {
        if (this.store.getToken() === null) {
        this.store.signOut();
        return 'revoked';
        }
        try {
        await logout(this.client);
        return 'revoked';
        } catch (error) {
        if (error instanceof ApiError && error.status === 401) return 'revoked';
        return 'not_confirmed';
        } finally {
        this.store.signOut();
        }
    }
}