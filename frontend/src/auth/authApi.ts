import type { ApiClient } from '../api/client';
import { ApiError } from '../api/errors';
import type { Session, User } from './session';

export interface Challenge {
    challenge_id: string;
    nonce: string;
    expires_at: string;
}

export interface Me {
    user: User;
    owned_workspace_count: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
    return typeof value === 'string' && value !== '' ? value : null;
}

function parseUser(value: unknown): User | null {
    if (!isRecord(value)) return null;
    const id = str(value['id']);
    const displayName = str(value['display_name']);
    const email = str(value['email']);
    const createdAt = str(value['created_at']);
    if (!id || !displayName || !email || !createdAt) return null;
    return { id, display_name: displayName, email, created_at: createdAt };
}

function parseChallenge(value: unknown): Challenge | null {
    if (!isRecord(value)) return null;
    const challengeId = str(value['challenge_id']);
    const nonce = str(value['nonce']);
    const expiresAt = str(value['expires_at']);
    if (!challengeId || !nonce || !expiresAt) return null;
    return { challenge_id: challengeId, nonce, expires_at: expiresAt };
}

function parseSession(value: unknown): Session | null {
    if (!isRecord(value)) return null;
    const token = str(value['access_token']);
    const expiresAt = str(value['expires_at']);
    const user = parseUser(value['user']);
    if (!token || !expiresAt || !user || value['token_type'] !== 'Bearer') return null;
    return { access_token: token, token_type: 'Bearer', expires_at: expiresAt, user };
}

function parseMe(value: unknown): Me | null {
    if (!isRecord(value)) return null;
    const user = parseUser(value['user']);
    const count = value['owned_workspace_count'];
    if (!user || typeof count !== 'number' || !Number.isInteger(count) || count < 0) return null;
    return { user, owned_workspace_count: count };
}

function invalidResponse(status: number, requestId: string | undefined): ApiError {
    return new ApiError({
        status,
        code: 'INVALID_RESPONSE',
        message: 'The service returned an unexpected response.',
        requestId,
    });
}

/** Step 1 of sign-in: ask Identity for a single-use nonce. No body and no token. */
export async function createChallenge(client: ApiClient): Promise<Challenge> {
    const result = await client.post<unknown>('/auth/google/challenges', { auth: false });
    const challenge = parseChallenge(result.data);
    if (!challenge) throw invalidResponse(result.status, result.requestId);
    return challenge;
}

/** Step 3 of sign-in: trade the Google ID token for an application session. */
export async function exchangeCredential(
    client: ApiClient,
    input: { challengeId: string; idToken: string },
): Promise<Session> {
    const result = await client.post<unknown>('/auth/google/exchange', {
        auth: false,
        body: { challenge_id: input.challengeId, id_token: input.idToken },
    });
    const session = parseSession(result.data);
    if (!session) throw invalidResponse(result.status, result.requestId);
    return session;
}

export async function fetchMe(client: ApiClient): Promise<Me> {
    const result = await client.get<unknown>('/me');
    const me = parseMe(result.data);
    if (!me) throw invalidResponse(result.status, result.requestId);
    return me;
}

export async function logout(client: ApiClient): Promise<void> {
    await client.post('/auth/logout');
}