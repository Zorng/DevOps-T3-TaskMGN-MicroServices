import type { ApiClient } from '../api/client';
import { CreateAttempt } from '../api/mutations';
import { invalidResponse, isRecord, positiveInt, str } from '../api/parse';
import { parseUser, type UserSummary } from '../tasks/tasksApi';
import type { CommentRequest } from './mentions';

export interface CommentMention {
    user: UserSummary;
    start: number;
    length: number;
}

export interface Comment {
    id: string;
    workspace_id: string;
    task_id: string;
    body: string;
    author: UserSummary;
    mentions: CommentMention[];
    created_at: string;
}

function parseMention(value: unknown): CommentMention | null {
    if (!isRecord(value)) return null;
    const user = parseUser(value['user']);
    const length = positiveInt(value['length']);
    const start = value['start'];
    if (!user || !length || typeof start !== 'number' || !Number.isInteger(start) || start < 0) return null;
    return { user, start, length };
}

export function parseComment(value: unknown): Comment | null {
    if (!isRecord(value)) return null;
    const id = str(value['id']);
    const workspaceId = str(value['workspace_id']);
    const taskId = str(value['task_id']);
    const body = str(value['body']);
    const author = parseUser(value['author']);
    const createdAt = str(value['created_at']);
    const rawMentions = value['mentions'];
    if (!id || !workspaceId || !taskId || !body || !author || !createdAt || !Array.isArray(rawMentions)) return null;
    const mentions: CommentMention[] = [];
    for (const raw of rawMentions) {
        const mention = parseMention(raw);
        if (!mention) return null;
        mentions.push(mention);
    }
    return { id, workspace_id: workspaceId, task_id: taskId, body, author, mentions, created_at: createdAt };
}

/** Oldest first, as the contract orders them (created_at ascending, then id). */
export const commentsPath = (workspaceId: string, taskId: string) =>
    `/workspaces/${encodeURIComponent(workspaceId)}/tasks/${encodeURIComponent(taskId)}/comments`;

/** One create intention = one idempotency key. Call send() again after an unclear failure. There is no edit or delete. */
export function createCommentAttempt(client: ApiClient, workspaceId: string, taskId: string, request: CommentRequest) {
    const attempt = new CreateAttempt<unknown>(client, commentsPath(workspaceId, taskId), request);
    return {
        attempt,
        async send(options: { signal?: AbortSignal } = {}): Promise<Comment> {
            const result = await attempt.send(options);
            const comment = parseComment(result.data);
            if (!comment) throw invalidResponse(result.status, result.requestId);
            return comment;
        },
    };
}
