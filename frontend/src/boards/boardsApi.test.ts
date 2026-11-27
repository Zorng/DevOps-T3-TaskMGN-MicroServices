import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { createApiClient } from '../api/client';
import { isVersionConflict } from '../api/mutations';
import { TEST_API_BASE_URL } from '../test/handlers';
import { BOARD_ID, boardFixture, errorBodyFixture, WORKSPACE_ID } from '../test/fixtures';
import { server } from '../test/server';
import {
    BOARD_NAME_MAX,
    boardRoute,
    checkBoardName,
    createBoardAttempt,
    deleteBoard,
    getBoard,
    listBoards,
    renameBoard,
} from './boardsApi';

const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'test-token' });
const boardsUrl = `${TEST_API_BASE_URL}/api/v1/workspaces/${WORKSPACE_ID}/boards`;
const boardUrl = `${boardsUrl}/${BOARD_ID}`;

describe('checkBoardName', () => {
    it('trims the name', () => {
        expect(checkBoardName('  Todo  ')).toEqual({ ok: true, name: 'Todo' });
    });

    it('rejects an empty or blank name', () => {
        expect(checkBoardName('')).toMatchObject({ ok: false });
        expect(checkBoardName('   ')).toMatchObject({ ok: false });
    });

    it('counts Unicode code points, not UTF-16 units', () => {
        // Each emoji is one code point but two UTF-16 units.
        expect(checkBoardName('😀'.repeat(BOARD_NAME_MAX))).toMatchObject({ ok: true });
        expect(checkBoardName('😀'.repeat(BOARD_NAME_MAX + 1))).toMatchObject({ ok: false });
        expect(checkBoardName('x'.repeat(BOARD_NAME_MAX + 1))).toMatchObject({ ok: false });
    });
});

describe('boardRoute', () => {
    it('keeps both the workspace and the board in the address', () => {
        expect(boardRoute(WORKSPACE_ID, BOARD_ID)).toBe(`/workspaces/${WORKSPACE_ID}/boards/${BOARD_ID}`);
    });
});

describe('listBoards', () => {
    it('returns the boards in the order the server sent them', async () => {
        server.use(
            http.get(boardsUrl, () =>
                HttpResponse.json({ items: [boardFixture(), boardFixture({ id: 'second', name: 'Done' })] }),
            ),
        );
        const boards = await listBoards(client, WORKSPACE_ID);
        expect(boards.map((board) => board.name)).toEqual(['Todo', 'Done']);
    });

    it('returns an empty list for a workspace without boards', async () => {
        server.use(http.get(boardsUrl, () => HttpResponse.json({ items: [] })));
        expect(await listBoards(client, WORKSPACE_ID)).toEqual([]);
    });

    it('rejects a response that is not a board list', async () => {
        server.use(http.get(boardsUrl, () => HttpResponse.json({ items: [{ id: 'x' }] })));
        await expect(listBoards(client, WORKSPACE_ID)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
    });
});

describe('getBoard', () => {
    it('returns the board and the server ETag', async () => {
        server.use(http.get(boardUrl, () => HttpResponse.json(boardFixture(), { headers: { ETag: '"1"' } })));
        const result = await getBoard(client, WORKSPACE_ID, BOARD_ID);
        expect(result.board.name).toBe('Todo');
        expect(result.etag).toBe('"1"');
    });

    it('falls back to the quoted version when the ETag header is missing', async () => {
        server.use(http.get(boardUrl, () => HttpResponse.json(boardFixture({ version: 4 }))));
        expect((await getBoard(client, WORKSPACE_ID, BOARD_ID)).etag).toBe('"4"');
    });

    it('passes through the contract error for a deleted board', async () => {
        server.use(http.get(boardUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })));
        await expect(getBoard(client, WORKSPACE_ID, BOARD_ID)).rejects.toMatchObject({ status: 404, code: 'NOT_FOUND' });
    });
});

describe('createBoardAttempt', () => {
    it('sends the name with an Idempotency-Key', async () => {
        let seen: { key: string | null; body: unknown } | undefined;
        server.use(
            http.post(boardsUrl, async ({ request }) => {
                seen = { key: request.headers.get('idempotency-key'), body: await request.json() };
                return HttpResponse.json(boardFixture(), { status: 201, headers: { ETag: '"1"' } });
            }),
        );
        const create = createBoardAttempt(client, WORKSPACE_ID, 'Todo');
        const result = await create.send();
        expect(result.board.name).toBe('Todo');
        expect(seen?.key).toBe(create.attempt.idempotencyKey);
        expect(seen?.body).toEqual({ name: 'Todo' });
    });

    it('reuses the same key when retried after an unclear failure', async () => {
        const keys: (string | null)[] = [];
        server.use(
            http.post(boardsUrl, ({ request }) => {
                keys.push(request.headers.get('idempotency-key'));
                if (keys.length === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                }
                return HttpResponse.json(boardFixture(), { status: 201 });
            }),
        );
        const create = createBoardAttempt(client, WORKSPACE_ID, 'Todo');
        await expect(create.send()).rejects.toMatchObject({ status: 503 });
        expect(create.attempt.state).toBe('retryable');
        await create.send();
        expect(keys[0]).toBe(keys[1]);
    });

    it('surfaces BOARD_LIMIT_REACHED', async () => {
        server.use(http.post(boardsUrl, () => HttpResponse.json(errorBodyFixture('BOARD_LIMIT_REACHED'), { status: 409 })));
        await expect(createBoardAttempt(client, WORKSPACE_ID, 'Sixth').send()).rejects.toMatchObject({
            status: 409,
            code: 'BOARD_LIMIT_REACHED',
        });
    });
});

describe('renameBoard', () => {
    it('sends If-Match and the new name, and returns the new version', async () => {
        let seen: { ifMatch: string | null; body: unknown } | undefined;
        server.use(
            http.patch(boardUrl, async ({ request }) => {
                seen = { ifMatch: request.headers.get('if-match'), body: await request.json() };
                return HttpResponse.json(boardFixture({ name: 'Doing', version: 2 }), { headers: { ETag: '"2"' } });
            }),
        );
        const result = await renameBoard(client, WORKSPACE_ID, BOARD_ID, 'Doing', '"1"');
        expect(seen).toEqual({ ifMatch: '"1"', body: { name: 'Doing' } });
        expect(result.board.name).toBe('Doing');
        expect(result.etag).toBe('"2"');
    });

    it('reports a stale version as a version conflict', async () => {
        server.use(http.patch(boardUrl, () => HttpResponse.json(errorBodyFixture('VERSION_CONFLICT'), { status: 412 })));
        const error = await renameBoard(client, WORKSPACE_ID, BOARD_ID, 'Doing', '"1"').catch((e: unknown) => e);
        expect(isVersionConflict(error)).toBe(true);
    });
});

describe('deleteBoard', () => {
    it('sends If-Match and accepts 204', async () => {
        let ifMatch: string | null = null;
        server.use(
            http.delete(boardUrl, ({ request }) => {
                ifMatch = request.headers.get('if-match');
                return new HttpResponse(null, { status: 204 });
            }),
        );
        await deleteBoard(client, WORKSPACE_ID, BOARD_ID, '"3"');
        expect(ifMatch).toBe('"3"');
    });

    it('passes through a refusal for a member', async () => {
        server.use(http.delete(boardUrl, () => HttpResponse.json(errorBodyFixture('FORBIDDEN'), { status: 403 })));
        await expect(deleteBoard(client, WORKSPACE_ID, BOARD_ID, '"1"')).rejects.toMatchObject({
            status: 403,
            code: 'FORBIDDEN',
        });
    });
});
