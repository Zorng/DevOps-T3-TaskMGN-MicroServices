import type { ApiClient, ApiResponse } from '../api/client';
import { CreateAttempt, deleteWithVersion, updateWithVersion } from '../api/mutations';
import { invalidResponse, isRecord, positiveInt, str } from '../api/parse';

export const WORKSPACE_NAME_MAX = 100;
export const OWNED_WORKSPACE_LIMIT = 10;
export const WORKSPACE_MEMBER_LIMIT = 20;

export type Role = 'owner' | 'member';

export interface Workspace {
    id: string;
    name: string;
    owner_id: string;
    my_role: Role;
    member_count: number;
    version: number;
    created_at: string;
    updated_at: string;
}

/** A workspace plus the ETag to send back as If-Match on rename or delete. */
export interface VersionedWorkspace {
    workspace: Workspace;
    etag: string;
}

/** The contract defines the ETag as the quoted version, e.g. "3". Used when a list item has no header. */
export function etagForVersion(version: number): string {
    return `"${version}"`;
}

export function parseWorkspace(value: unknown): Workspace | null {
    if (!isRecord(value)) return null;
    const id = str(value['id']);
    const name = str(value['name']);
    const ownerId = str(value['owner_id']);
    const role = value['my_role'];
    const memberCount = positiveInt(value['member_count']);
    const version = positiveInt(value['version']);
    const createdAt = str(value['created_at']);
    const updatedAt = str(value['updated_at']);
    if (!id || !name || !ownerId || !memberCount || !version || !createdAt || !updatedAt) return null;
    if (role !== 'owner' && role !== 'member') return null;
    return {
        id,
        name,
        owner_id: ownerId,
        my_role: role,
        member_count: memberCount,
        version,
        created_at: createdAt,
        updated_at: updatedAt,
    };
}

function toVersioned(result: ApiResponse<unknown>): VersionedWorkspace {
    const workspace = parseWorkspace(result.data);
    if (!workspace) throw invalidResponse(result.status, result.requestId);
    return { workspace, etag: result.etag ?? etagForVersion(workspace.version) };
}

export type NameCheck = { ok: true; name: string } | { ok: false; message: string };

/** Trims, then checks 1 to 100 Unicode code points (not UTF-16 units), as the contract requires. */
export function checkWorkspaceName(raw: string): NameCheck {
    const name = raw.trim();
    if (name === '') return { ok: false, message: 'Enter a workspace name.' };
    if ([...name].length > WORKSPACE_NAME_MAX) {
        return { ok: false, message: `Use ${WORKSPACE_NAME_MAX} characters or fewer.` };
    }
    return { ok: true, name };
}

const workspacePath = (id: string) => `/workspaces/${encodeURIComponent(id)}`;

/**
 * One create intention = one idempotency key. Call send() again after an unclear failure
 * (it reuses the key); make a new attempt only when the user changes the name.
 */
export function createWorkspaceAttempt(client: ApiClient, name: string) {
    const attempt = new CreateAttempt<unknown>(client, '/workspaces', { name });
    return {
        attempt,
        async send(options: { signal?: AbortSignal } = {}): Promise<VersionedWorkspace> {
            return toVersioned(await attempt.send(options));
        },
    };
}

export async function getWorkspace(
    client: ApiClient,
    id: string,
    signal?: AbortSignal,
): Promise<VersionedWorkspace> {
    return toVersioned(await client.get<unknown>(workspacePath(id), { signal }));
}

/** Owner only. `etag` is the value from the last read; a stale one gives 412 VERSION_CONFLICT. */
export async function renameWorkspace(
    client: ApiClient,
    id: string,
    name: string,
    etag: string,
): Promise<VersionedWorkspace> {
    return toVersioned(await updateWithVersion<unknown>(client, workspacePath(id), { name }, etag));
}

/** Owner only. Same ETag rule as rename. */
export async function deleteWorkspace(client: ApiClient, id: string, etag: string): Promise<void> {
    await deleteWithVersion(client, workspacePath(id), etag);
}

export interface Member {
    membership_id: string;
    user: { id: string; display_name: string };
    role: Role;
    joined_at: string;
}

export function parseMember(value: unknown): Member | null {
    if (!isRecord(value)) return null;
    const membershipId = str(value['membership_id']);
    const joinedAt = str(value['joined_at']);
    const role = value['role'];
    const user = value['user'];
    if (!membershipId || !joinedAt || !isRecord(user)) return null;
    const userId = str(user['id']);
    const displayName = str(user['display_name']);
    if (!userId || !displayName) return null;
    if (role !== 'owner' && role !== 'member') return null;
    return {
        membership_id: membershipId,
        user: { id: userId, display_name: displayName },
        role,
        joined_at: joinedAt,
    };
}

/** Members are not paginated: a workspace has at most 20. */
export async function listMembers(
    client: ApiClient,
    workspaceId: string,
    signal?: AbortSignal,
): Promise<Member[]> {
    const result = await client.get<unknown>(`${workspacePath(workspaceId)}/members`, { signal });
    const items: unknown = isRecord(result.data) ? result.data['items'] : undefined;
    if (!Array.isArray(items)) throw invalidResponse(result.status, result.requestId);
    const members: Member[] = [];
    for (const item of items) {
        const member = parseMember(item);
        if (!member) throw invalidResponse(result.status, result.requestId);
        members.push(member);
    }
    return members;
}

/** Owner only. No If-Match is needed for membership changes. */
export async function removeMember(client: ApiClient, workspaceId: string, userId: string): Promise<void> {
    await client.delete(`${workspacePath(workspaceId)}/members/${encodeURIComponent(userId)}`);
}

/** A non-owner leaves. The owner gets 409 OWNER_CANNOT_LEAVE. */
export async function leaveWorkspace(client: ApiClient, workspaceId: string): Promise<void> {
    await client.delete(`${workspacePath(workspaceId)}/membership`);
}