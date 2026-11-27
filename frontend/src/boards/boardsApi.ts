import type { ApiClient, ApiResponse } from '../api/client';
import { CreateAttempt, deleteWithVersion, updateWithVersion } from '../api/mutations';
import { invalidResponse, isRecord, positiveInt, str } from '../api/parse';
import { etagForVersion } from '../workspaces/workspacesApi';

export const BOARD_NAME_MAX = 50;
export const BOARD_LIMIT = 5;

export interface Board {
    id: string;
    workspace_id: string;
    name: string;
    version: number;
    created_at: string;
    updated_at: string;
}

/** A board plus the ETag to send back as If-Match on rename or delete. */
export interface VersionedBoard {
    board: Board;
    etag: string;
}

export function parseBoard(value: unknown): Board | null {
    if (!isRecord(value)) return null;
    const id = str(value['id']);
    const workspaceId = str(value['workspace_id']);
    const name = str(value['name']);
    const version = positiveInt(value['version']);
    const createdAt = str(value['created_at']);
    const updatedAt = str(value['updated_at']);
    if (!id || !workspaceId || !name || !version || !createdAt || !updatedAt) return null;
    return { id, workspace_id: workspaceId, name, version, created_at: createdAt, updated_at: updatedAt };
}

function toVersioned(result: ApiResponse<unknown>): VersionedBoard {
    const board = parseBoard(result.data);
    if (!board) throw invalidResponse(result.status, result.requestId);
    return { board, etag: result.etag ?? etagForVersion(board.version) };
}

export type BoardNameCheck = { ok: true; name: string } | { ok: false; message: string };

/** Trims, then checks 1 to 50 Unicode code points (not UTF-16 units), as the contract requires. */
export function checkBoardName(raw: string): BoardNameCheck {
    const name = raw.trim();
    if (name === '') return { ok: false, message: 'Enter a board name.' };
    if ([...name].length > BOARD_NAME_MAX) {
        return { ok: false, message: `Use ${BOARD_NAME_MAX} characters or fewer.` };
    }
    return { ok: true, name };
}

export const boardsPath = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}/boards`;
const boardPath = (workspaceId: string, boardId: string) =>
    `${boardsPath(workspaceId)}/${encodeURIComponent(boardId)}`;

/** The browser address of a board, so links keep both the workspace and the board. */
export const boardRoute = (workspaceId: string, boardId: string) =>
    `/workspaces/${encodeURIComponent(workspaceId)}/boards/${encodeURIComponent(boardId)}`;

/**
 * Boards are not paginated: a workspace has at most five, in creation order.
 * A workspace may have none; nothing is created for it automatically.
 */
export async function listBoards(client: ApiClient, workspaceId: string, signal?: AbortSignal): Promise<Board[]> {
    const result = await client.get<unknown>(boardsPath(workspaceId), { signal });
    const items: unknown = isRecord(result.data) ? result.data['items'] : undefined;
    if (!Array.isArray(items)) throw invalidResponse(result.status, result.requestId);
    const boards: Board[] = [];
    for (const item of items) {
        const board = parseBoard(item);
        if (!board) throw invalidResponse(result.status, result.requestId);
        boards.push(board);
    }
    return boards;
}

export async function getBoard(
    client: ApiClient,
    workspaceId: string,
    boardId: string,
    signal?: AbortSignal,
): Promise<VersionedBoard> {
    return toVersioned(await client.get<unknown>(boardPath(workspaceId, boardId), { signal }));
}

/** One create intention = one idempotency key. Call send() again after an unclear failure. */
export function createBoardAttempt(client: ApiClient, workspaceId: string, name: string) {
    const attempt = new CreateAttempt<unknown>(client, boardsPath(workspaceId), { name });
    return {
        attempt,
        async send(options: { signal?: AbortSignal } = {}): Promise<VersionedBoard> {
            return toVersioned(await attempt.send(options));
        },
    };
}

/** Owners and members. `etag` is from the last read; a stale one gives 412 VERSION_CONFLICT. */
export async function renameBoard(
    client: ApiClient,
    workspaceId: string,
    boardId: string,
    name: string,
    etag: string,
): Promise<VersionedBoard> {
    return toVersioned(await updateWithVersion<unknown>(client, boardPath(workspaceId, boardId), { name }, etag));
}

/** Owner only. Removes the board with its tasks and comments. Same ETag rule as rename. */
export async function deleteBoard(
    client: ApiClient,
    workspaceId: string,
    boardId: string,
    etag: string,
): Promise<void> {
    await deleteWithVersion(client, boardPath(workspaceId, boardId), etag);
}
