import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { createApiClient } from '../api/client';
import { ApiError } from '../api/errors';
import { isVersionConflict } from '../api/mutations';
import { TEST_API_BASE_URL } from '../test/handlers';
import { errorBodyFixture, WORKSPACE_ID, workspaceFixture } from '../test/fixtures';
import { server } from '../test/server';
import {
    checkWorkspaceName,
    createWorkspaceAttempt,
    deleteWorkspace,
    getWorkspace,
    renameWorkspace,
} from './workspacesApi';

const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'test-token' });
const url = (path: string) => `${TEST_API_BASE_URL}/api/v1${path}`;
const workspaceUrl = url(`/workspaces/${WORKSPACE_ID}`);

describe('getWorkspace', () => {
    it('returns the workspace and the server ETag', async () => {
        server.use(
            http.get(workspaceUrl, () => HttpResponse.json(workspaceFixture(), { headers: { ETag: '"2"' } })),
        );
        const result = await getWorkspace(client, WORKSPACE_ID);
        expect(result.workspace.name).toBe('DevOps Team');
        expect(result.workspace.my_role).toBe('owner');
        expect(result.etag).toBe('"2"');
    });

    it('falls back to the quoted version when the ETag header is missing', async () => {
        server.use(http.get(workspaceUrl, () => HttpResponse.json(workspaceFixture({ version: 5 }))));
        expect((await getWorkspace(client, WORKSPACE_ID)).etag).toBe('"5"');
    });

    it('rejects a response that is not a workspace', async () => {
        server.use(http.get(workspaceUrl, () => HttpResponse.json({ id: 'x' })));
        await expect(getWorkspace(client, WORKSPACE_ID)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
    });

    it('passes through the contract error for a hidden workspace', async () => {
        server.use(http.get(workspaceUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })));
        await expect(getWorkspace(client, WORKSPACE_ID)).rejects.toMatchObject({ status: 404, code: 'NOT_FOUND' });
    });
});

describe('renameWorkspace', () => {
    it('sends the name with If-Match and returns the new version', async () => {
        let seen: { ifMatch: string | null; body: unknown } | undefined;
        server.use(
            http.patch(workspaceUrl, async ({ request }) => {
                seen = { ifMatch: request.headers.get('if-match'), body: await request.json() };
                return HttpResponse.json(workspaceFixture({ name: 'Renamed', version: 3 }), {
                    headers: { ETag: '"3"' },
                });
            }),
        );
        const result = await renameWorkspace(client, WORKSPACE_ID, 'Renamed', '"2"');
        expect(seen).toEqual({ ifMatch: '"2"', body: { name: 'Renamed' } });
        expect(result.workspace.name).toBe('Renamed');
        expect(result.etag).toBe('"3"');
    });

    it('reports a stale version as a version conflict', async () => {
        server.use(
            http.patch(workspaceUrl, () => HttpResponse.json(errorBodyFixture('VERSION_CONFLICT'), { status: 412 })),
        );
        const error = await renameWorkspace(client, WORKSPACE_ID, 'Renamed', '"1"').catch((e: unknown) => e);
        expect(isVersionConflict(error)).toBe(true);
    });
});

describe('deleteWorkspace', () => {
    it('sends If-Match and resolves on 204', async () => {
        let ifMatch: string | null = null;
        server.use(
            http.delete(workspaceUrl, ({ request }) => {
                ifMatch = request.headers.get('if-match');
                return new HttpResponse(null, { status: 204 });
            }),
        );
        await expect(deleteWorkspace(client, WORKSPACE_ID, '"2"')).resolves.toBeUndefined();
        expect(ifMatch).toBe('"2"');
    });
});

describe('createWorkspaceAttempt', () => {
    it('reuses the same Idempotency-Key when retrying after an unclear failure', async () => {
        const keys: (string | null)[] = [];
        server.use(
            http.post(url('/workspaces'), ({ request }) => {
                keys.push(request.headers.get('idempotency-key'));
                if (keys.length === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), {
                        status: 503,
                        headers: { 'Retry-After': '1' },
                    });
                }
                return HttpResponse.json(workspaceFixture({ version: 1, member_count: 1 }), {
                    status: 201,
                    headers: { ETag: '"1"' },
                });
            }),
        );
        const create = createWorkspaceAttempt(client, 'DevOps Team');
        await expect(create.send()).rejects.toBeInstanceOf(ApiError);
        const created = await create.send();
        expect(keys).toHaveLength(2);
        expect(keys[0]).toBeTruthy();
        expect(keys[1]).toBe(keys[0]);
        expect(created.workspace.id).toBe(WORKSPACE_ID);
        expect(created.etag).toBe('"1"');
    });

    it('treats the ten-owned-workspaces rejection as final and does not resend', async () => {
        let requests = 0;
        server.use(
            http.post(url('/workspaces'), () => {
                requests += 1;
                return HttpResponse.json(errorBodyFixture('WORKSPACE_LIMIT_REACHED'), { status: 409 });
            }),
        );
        const create = createWorkspaceAttempt(client, 'One too many');
        await expect(create.send()).rejects.toMatchObject({ code: 'WORKSPACE_LIMIT_REACHED' });
        await expect(create.send()).rejects.toMatchObject({ code: 'WORKSPACE_LIMIT_REACHED' });
        expect(requests).toBe(1);
    });
});

describe('checkWorkspaceName', () => {
    it('trims the name', () => {
        expect(checkWorkspaceName('  Team  ')).toEqual({ ok: true, name: 'Team' });
    });

    it.each(['', '   '])('rejects an empty name: %j', (raw) => {
        expect(checkWorkspaceName(raw).ok).toBe(false);
    });

    it('allows 100 characters and rejects 101', () => {
        expect(checkWorkspaceName('x'.repeat(100)).ok).toBe(true);
        expect(checkWorkspaceName('x'.repeat(101)).ok).toBe(false);
    });

    it('counts Unicode code points, not UTF-16 units', () => {
        // 100 emoji are 200 UTF-16 units but exactly 100 code points.
        expect(checkWorkspaceName('😀'.repeat(100)).ok).toBe(true);
        expect(checkWorkspaceName('😀'.repeat(101)).ok).toBe(false);
    });
});