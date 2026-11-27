import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { createApiClient } from '../api/client';
import { TEST_API_BASE_URL } from '../test/handlers';
import { commentFixture, errorBodyFixture, MEMBER_ID, TASK_ID, WORKSPACE_ID } from '../test/fixtures';
import { server } from '../test/server';
import { commentsPath, createCommentAttempt, parseComment } from './commentsApi';

const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'test-token' });
const commentsUrl = `${TEST_API_BASE_URL}/api/v1${commentsPath(WORKSPACE_ID, TASK_ID)}`;

describe('commentsPath', () => {
    it('is nested under the task', () => {
        expect(commentsPath(WORKSPACE_ID, TASK_ID)).toBe(`/workspaces/${WORKSPACE_ID}/tasks/${TASK_ID}/comments`);
    });
});

describe('parseComment', () => {
    it('reads the contract example', () => {
        expect(parseComment(commentFixture())).toMatchObject({
            body: 'Please review @Dara',
            author: { display_name: 'Example Owner' },
            mentions: [{ user: { display_name: 'Dara' }, start: 14, length: 5 }],
        });
    });

    it('accepts a comment without mentions', () => {
        expect(parseComment(commentFixture({ mentions: [] }))?.mentions).toEqual([]);
    });

    it('rejects values that are not comments', () => {
        expect(parseComment({ id: 'x' })).toBeNull();
        expect(parseComment(commentFixture({ body: '' }))).toBeNull();
        expect(parseComment(commentFixture({ mentions: [{ user: { id: 'x' }, start: 1, length: 2 }] }))).toBeNull();
        expect(parseComment(commentFixture({ mentions: 'none' }))).toBeNull();
    });
});

describe('createCommentAttempt', () => {
    const request = { body: 'Please review @Dara', mentions: [{ user_id: MEMBER_ID, start: 14, length: 5 }] };

    it('sends the body and the id/range pairs with an Idempotency-Key', async () => {
        let seen: { key: string | null; body: unknown } | undefined;
        server.use(
            http.post(commentsUrl, async ({ request: req }) => {
                seen = { key: req.headers.get('idempotency-key'), body: await req.json() };
                return HttpResponse.json(commentFixture(), { status: 201 });
            }),
        );
        const create = createCommentAttempt(client, WORKSPACE_ID, TASK_ID, request);
        expect((await create.send()).body).toBe('Please review @Dara');
        expect(seen?.key).toBe(create.attempt.idempotencyKey);
        expect(seen?.body).toEqual(request);
    });

    it('reuses the same key when retried after an unclear failure', async () => {
        const keys: (string | null)[] = [];
        server.use(
            http.post(commentsUrl, ({ request: req }) => {
                keys.push(req.headers.get('idempotency-key'));
                if (keys.length === 1) return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                return HttpResponse.json(commentFixture(), { status: 201 });
            }),
        );
        const create = createCommentAttempt(client, WORKSPACE_ID, TASK_ID, request);
        await expect(create.send()).rejects.toMatchObject({ status: 503 });
        await create.send();
        expect(keys[0]).toBe(keys[1]);
    });

    it('passes through a 422 for a removed mention target', async () => {
        server.use(http.post(commentsUrl, () => HttpResponse.json(errorBodyFixture('VALIDATION_FAILED'), { status: 422 })));
        await expect(createCommentAttempt(client, WORKSPACE_ID, TASK_ID, request).send()).rejects.toMatchObject({
            status: 422,
            code: 'VALIDATION_FAILED',
        });
    });
});
