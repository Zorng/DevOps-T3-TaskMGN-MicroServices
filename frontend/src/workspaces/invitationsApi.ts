import type { ApiClient, ApiResponse } from '../api/client';
import { CreateAttempt } from '../api/mutations';
import { invalidResponse, isRecord, str } from '../api/parse';

export const INVITE_EMAIL_MAX = 254;

export type InvitationStatus = 'pending' | 'accepted' | 'declined' | 'cancelled';

export interface UserSummary {
    id: string;
    display_name: string;
}

export interface Invitation {
    id: string;
    workspace_id: string;
    workspace_name: string;
    inviter: UserSummary;
    recipient: UserSummary;
    status: InvitationStatus;
    created_at: string;
    updated_at: string;
}

function parseUserSummary(value: unknown): UserSummary | null {
    if (!isRecord(value)) return null;
    const id = str(value['id']);
    const displayName = str(value['display_name']);
    return id && displayName ? { id, display_name: displayName } : null;
}

const STATUSES: readonly string[] = ['pending', 'accepted', 'declined', 'cancelled'];

export function parseInvitation(value: unknown): Invitation | null {
    if (!isRecord(value)) return null;
    const id = str(value['id']);
    const workspaceId = str(value['workspace_id']);
    const workspaceName = str(value['workspace_name']);
    const inviter = parseUserSummary(value['inviter']);
    const recipient = parseUserSummary(value['recipient']);
    const status = value['status'];
    const createdAt = str(value['created_at']);
    const updatedAt = str(value['updated_at']);
    if (!id || !workspaceId || !workspaceName || !inviter || !recipient || !createdAt || !updatedAt) return null;
    if (typeof status !== 'string' || !STATUSES.includes(status)) return null;
    return {
        id,
        workspace_id: workspaceId,
        workspace_name: workspaceName,
        inviter,
        recipient,
        status: status as InvitationStatus,
        created_at: createdAt,
        updated_at: updatedAt,
    };
}

function toInvitation(result: ApiResponse<unknown>): Invitation {
    const invitation = parseInvitation(result.data);
    if (!invitation) throw invalidResponse(result.status, result.requestId);
    return invitation;
}

export type EmailCheck = { ok: true; email: string } | { ok: false; message: string };

/** Trims, then checks length and a basic address shape. The server does the exact registered-user lookup. */
export function checkInviteEmail(raw: string): EmailCheck {
    const email = raw.trim();
    if (email === '') return { ok: false, message: 'Enter an email address.' };
    if ([...email].length > INVITE_EMAIL_MAX) {
        return { ok: false, message: `Use ${INVITE_EMAIL_MAX} characters or fewer.` };
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        return { ok: false, message: 'Enter a valid email address, like name@example.com.' };
    }
    return { ok: true, email };
}

export const invitationsPath = (workspaceId: string) =>
    `/workspaces/${encodeURIComponent(workspaceId)}/invitations`;

/** One invite intention = one idempotency key; send() again after an unclear failure reuses it. */
export function createInvitationAttempt(client: ApiClient, workspaceId: string, email: string) {
  const attempt = new CreateAttempt<unknown>(client, invitationsPath(workspaceId), { email });
    return {
        attempt,
        async send(options: { signal?: AbortSignal } = {}): Promise<Invitation> {
            return toInvitation(await attempt.send(options));
        },
    };
}

/** Owner only. State-based, so it needs no Idempotency-Key or If-Match. */
export async function cancelInvitation(client: ApiClient, invitationId: string): Promise<Invitation> {
    return toInvitation(await client.post<unknown>(`/invitations/${encodeURIComponent(invitationId)}/cancel`));
}