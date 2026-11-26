import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { createApiClient } from '../api/client';
import { TEST_API_BASE_URL } from '../test/handlers';
import { errorBodyFixture, INVITATION_ID, invitationFixture, WORKSPACE_ID } from '../test/fixtures';
import { server } from '../test/server';
import { cancelInvitation, checkInviteEmail, createInvitationAttempt } from './invitationsApi';

const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'test-token' });
const url = (path: string) => `${TEST_API_BASE_URL}/api/v1${path}`;
const invitationsUrl = url(`/workspaces/${WORKSPACE_ID}/invitations`);

describe('createInvitationAttempt', () => {
    it('posts the email with an Idempotency-Key and returns the invitation', async () => {
        let seen: { key: string | null; body: unknown } | undefined;
        server.use(
            http.post(invitationsUrl, async ({ request }) => {
                seen = { key: request.headers.get('idempotency-key'), body: await request.json() };
                return HttpResponse.json(invitationFixture(), { status: 201 });
            }),
        );
        const invitation = await createInvitationAttempt(client, WORKSPACE_ID, 'dara@example.com').send();
        expect(seen?.body).toEqual({ email: 'dara@example.com' });
        expect(seen?.key).toBeTruthy();
        expect(invitation.recipient.display_name).toBe('Dara');
        expect(invitation.status).toBe('pending');
    });

    it('reuses the key when retrying after a temporary failure', async () => {
        const keys: (string | null)[] = [];
        server.use(
            http.post(invitationsUrl, ({ request }) => {
                keys.push(request.headers.get('idempotency-key'));
                if (keys.length === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), {
                        status: 503,
                        headers: { 'Retry-After': '1' },
                    });
                }
                return HttpResponse.json(invitationFixture(), { status: 201 });
            }),
        );
        const create = createInvitationAttempt(client, WORKSPACE_ID, 'dara@example.com');
        await expect(create.send()).rejects.toMatchObject({ code: 'DEPENDENCY_UNAVAILABLE' });
        await create.send();
        expect(keys[1]).toBe(keys[0]);
    });

    it('treats an unknown email as final and does not resend', async () => {
        let requests = 0;
        server.use(
            http.post(invitationsUrl, () => {
                requests += 1;
                return HttpResponse.json(errorBodyFixture('USER_NOT_FOUND'), { status: 404 });
            }),
        );
        const create = createInvitationAttempt(client, WORKSPACE_ID, 'nobody@example.com');
        await expect(create.send()).rejects.toMatchObject({ code: 'USER_NOT_FOUND' });
        await expect(create.send()).rejects.toMatchObject({ code: 'USER_NOT_FOUND' });
        expect(requests).toBe(1);
    });

    it('rejects a response that is not an invitation', async () => {
        server.use(http.post(invitationsUrl, () => HttpResponse.json({ id: 'x' }, { status: 201 })));
        await expect(createInvitationAttempt(client, WORKSPACE_ID, 'a@b.co').send()).rejects.toMatchObject({
            code: 'INVALID_RESPONSE',
        });
    });
});

describe('cancelInvitation', () => {
    it('posts to the cancel action and returns the updated invitation', async () => {
        let path: string | null = null;
        server.use(
            http.post(url(`/invitations/${INVITATION_ID}/cancel`), ({ request }) => {
                path = new URL(request.url).pathname;
                return HttpResponse.json(invitationFixture({ status: 'cancelled' }));
            }),
        );
        const invitation = await cancelInvitation(client, INVITATION_ID);
        expect(path).toBe(`/api/v1/invitations/${INVITATION_ID}/cancel`);
        expect(invitation.status).toBe('cancelled');
    });

    it('passes through the terminal-state conflict', async () => {
        server.use(
            http.post(url(`/invitations/${INVITATION_ID}/cancel`), () =>
                HttpResponse.json(errorBodyFixture('INVITATION_NOT_PENDING'), { status: 409 }),
            ),
        );
        await expect(cancelInvitation(client, INVITATION_ID)).rejects.toMatchObject({
            status: 409,
            code: 'INVITATION_NOT_PENDING',
        });
    });
});

describe('checkInviteEmail', () => {
    it('trims the address', () => {
        expect(checkInviteEmail('  dara@example.com ')).toEqual({ ok: true, email: 'dara@example.com' });
    });

    it.each(['', '   ', 'dara', 'dara@', '@example.com', 'dara@example', 'da ra@example.com'])(
        'rejects %j',
        (raw) => {
            expect(checkInviteEmail(raw).ok).toBe(false);
        },
    );

    it('rejects an address over 254 characters', () => {
        expect(checkInviteEmail(`${'a'.repeat(250)}@example.com`).ok).toBe(false);
    });
});