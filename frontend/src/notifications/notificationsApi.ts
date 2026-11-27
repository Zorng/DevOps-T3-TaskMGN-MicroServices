import type { ApiClient } from '../api/client';
import { invalidResponse, isRecord, str } from '../api/parse';
import { parseUser, type UserSummary } from '../tasks/tasksApi';

export const NOTIFICATIONS_PATH = '/notifications';

export type NotificationType = 'invitation' | 'assignment' | 'mention';
const TYPES: readonly string[] = ['invitation', 'assignment', 'mention'];

/**
 * Generic event metadata only: who did it, what kind of event, and which workspace, invitation or task it
 * concerns. It never carries a task title, a comment or an email, and it never grants access to its target.
 */
export interface Notification {
    id: string;
    event_id: string;
    type: NotificationType;
    actor: UserSummary;
    workspace_id: string;
    invitation_id: string | null;
    task_id: string | null;
    comment_id: string | null;
    occurred_at: string;
    created_at: string;
    read_at: string | null;
}

/** A string, null, or undefined when the value is neither (invalid). */
function nullableId(value: unknown): string | null | undefined {
    if (value === null) return null;
    return typeof value === 'string' && value !== '' ? value : undefined;
}

export function parseNotification(value: unknown): Notification | null {
    if (!isRecord(value)) return null;
    const id = str(value['id']);
    const eventId = str(value['event_id']);
    const type = value['type'];
    const actor = parseUser(value['actor']);
    const workspaceId = str(value['workspace_id']);
    const invitationId = nullableId(value['invitation_id']);
    const taskId = nullableId(value['task_id']);
    const commentId = nullableId(value['comment_id']);
    const occurredAt = str(value['occurred_at']);
    const createdAt = str(value['created_at']);
    const readAt = nullableId(value['read_at']);
    if (!id || !eventId || typeof type !== 'string' || !TYPES.includes(type) || !actor || !workspaceId) return null;
    if (invitationId === undefined || taskId === undefined || commentId === undefined || readAt === undefined) return null;
    if (!occurredAt || !createdAt) return null;
    return {
        id,
        event_id: eventId,
        type: type as NotificationType,
        actor,
        workspace_id: workspaceId,
        invitation_id: invitationId,
        task_id: taskId,
        comment_id: commentId,
        occurred_at: occurredAt,
        created_at: createdAt,
        read_at: readAt,
    };
}

/** The exact unread count for the signed-in user at the time of the request. */
export async function getUnreadCount(client: ApiClient, signal?: AbortSignal): Promise<number> {
    const result = await client.get<unknown>(`${NOTIFICATIONS_PATH}/unread-count`, { signal });
    const count = isRecord(result.data) ? result.data['count'] : undefined;
    if (typeof count !== 'number' || !Number.isInteger(count) || count < 0) {
        throw invalidResponse(result.status, result.requestId);
    }
    return count;
}

/**
 * Marks one of my notifications read. No body. The first call sets read_at and repeats keep it, so the
 * response is the authoritative state; another recipient's notification is a 404.
 */
export async function markNotificationRead(client: ApiClient, notificationId: string): Promise<Notification> {
    const result = await client.post<unknown>(`${NOTIFICATIONS_PATH}/${encodeURIComponent(notificationId)}/read`);
    const notification = parseNotification(result.data);
    if (!notification) throw invalidResponse(result.status, result.requestId);
    return notification;
}

export interface NotificationTarget {
    /** Where to open it. The destination page checks the user's current access itself. */
    to: string;
    label: string;
}

/** The page a notification opens, or null when it names none. Presence of a notification proves nothing about access. */
export function notificationTarget(notification: Notification): NotificationTarget | null {
    if (notification.type === 'invitation') {
        if (!notification.invitation_id) return null;
        return { to: `/invitations/${encodeURIComponent(notification.invitation_id)}`, label: 'Open invitation' };
    }
    if (!notification.task_id) return null;
    return {
        to: `/workspaces/${encodeURIComponent(notification.workspace_id)}/tasks/${encodeURIComponent(notification.task_id)}`,
        label: 'Open task',
    };
}

export const TYPE_LABEL: Record<NotificationType, string> = {
    invitation: 'Invitation',
    assignment: 'Assignment',
    mention: 'Mention',
};

/** Wording chosen from the type alone, as the contract says. */
export const TYPE_SENTENCE: Record<NotificationType, string> = {
    invitation: 'invited you to join a workspace',
    assignment: 'assigned you a task',
    mention: 'mentioned you in a comment',
};
