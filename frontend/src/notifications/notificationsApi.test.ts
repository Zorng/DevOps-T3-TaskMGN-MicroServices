import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { createApiClient } from '../api/client';
import { TEST_API_BASE_URL } from '../test/handlers';
import {
    errorBodyFixture,
    INVITATION_ID,
    NOTIFICATION_ID,
    notificationFixture,
    TASK_ID,
    WORKSPACE_ID,
} from '../test/fixtures';
import { server } from '../test/server';
import { getUnreadCount, markNotificationRead, notificationTarget, parseNotification } from './notificationsApi';

const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'test-token' });
const base = `${TEST_API_BASE_URL}/api/v1/notifications`;

describe('parseNotification', () => {
    it('reads the contract fields, including nulls', () => {
        expect(parseNotification(notificationFixture())).toMatchObject({
            type: 'assignment',
            actor: { display_name: 'Example Owner' },
            task_id: TASK_ID,
            invitation_id: null,
            comment_id: null,
            read_at: null,
        });
        expect(parseNotification(notificationFixture({ read_at: '2026-11-14T11:00:00Z' }))?.read_at).toBe('2026-11-14T11:00:00Z');
    });

    it('rejects an unknown type or a malformed value', () => {
        expect(parseNotification(notificationFixture({ type: 'reminder' }))).toBeNull();
        expect(parseNotification(notificationFixture({ actor: { id: 'x' } }))).toBeNull();
        expect(parseNotification(notificationFixture({ read_at: 5 }))).toBeNull();
        expect(parseNotification({ id: 'x' })).toBeNull();
    });
});

describe('getUnreadCount', () => {
    it('returns the exact count from the server', async () => {
        server.use(http.get(`${base}/unread-count`, () => HttpResponse.json({ count: 3 })));
        expect(await getUnreadCount(client)).toBe(3);
    });

    it('rejects a count that is not a non-negative integer', async () => {
        server.use(http.get(`${base}/unread-count`, () => HttpResponse.json({ count: -1 })));
        await expect(getUnreadCount(client)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
    });
});

describe('markNotificationRead', () => {
    it('posts to the read route with no body and returns the authoritative notification', async () => {
        let seen: { method: string; length: string | null } | undefined;
        server.use(
            http.post(`${base}/${NOTIFICATION_ID}/read`, ({ request }) => {
                seen = { method: request.method, length: request.headers.get('content-length') };
                return HttpResponse.json(notificationFixture({ read_at: '2026-11-14T11:00:00Z' }));
            }),
        );
        const result = await markNotificationRead(client, NOTIFICATION_ID);
        expect(result.read_at).toBe('2026-11-14T11:00:00Z');
        expect(seen?.method).toBe('POST');
        expect(seen?.length === null || seen?.length === '0').toBe(true);
    });

    it('passes through a 404 for a notification that is not mine', async () => {
        server.use(
            http.post(`${base}/${NOTIFICATION_ID}/read`, () =>
                HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 }),
            ),
        );
        await expect(markNotificationRead(client, NOTIFICATION_ID)).rejects.toMatchObject({ status: 404 });
    });
});

describe('notificationTarget', () => {
    it('opens the task for assignments and mentions', () => {
        const expected = { to: `/workspaces/${WORKSPACE_ID}/tasks/${TASK_ID}`, label: 'Open task' };
        expect(notificationTarget(parseNotification(notificationFixture())!)).toEqual(expected);
        expect(notificationTarget(parseNotification(notificationFixture({ type: 'mention' }))!)).toEqual(expected);
    });

    it('opens the invitation for invitation notifications', () => {
        const invitation = parseNotification(
            notificationFixture({ type: 'invitation', invitation_id: INVITATION_ID, task_id: null }),
        )!;
        expect(notificationTarget(invitation)).toEqual({ to: `/invitations/${INVITATION_ID}`, label: 'Open invitation' });
    });

    it('has no target when the notification names none', () => {
        expect(notificationTarget(parseNotification(notificationFixture({ task_id: null }))!)).toBeNull();
        expect(
            notificationTarget(parseNotification(notificationFixture({ type: 'invitation', task_id: null }))!),
        ).toBeNull();
    });
});
