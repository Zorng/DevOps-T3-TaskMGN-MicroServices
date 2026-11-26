import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { TEST_API_BASE_URL } from '../test/handlers';
import { server } from '../test/server';
import { createApiClient } from './client';
import { fetchPage, PageLoader } from './pagination';

const API = `${TEST_API_BASE_URL}/api/v1`;
const client = createApiClient({ baseUrl: TEST_API_BASE_URL, getToken: () => 'tok' });

const DATA = ['a', 'b', 'c', 'd', 'e'].map((id) => ({ id }));

/** A fake paginated endpoint: cursors look like "c2" (the offset). */
function pagedHandler(onRequest?: (url: URL) => void) {
    return http.get(`${API}/things`, ({ request }) => {
        const url = new URL(request.url);
        onRequest?.(url);
        const limit = Number(url.searchParams.get('limit') ?? 2);
        const start = Number((url.searchParams.get('cursor') ?? 'c0').slice(1));
        const end = start + limit;
        return HttpResponse.json({
            items: DATA.slice(start, end),
            next_cursor: end < DATA.length ? `c${end}` : null,
        });
    });
}

function errorBody(code: string) {
    return { error: { code, message: 'm', request_id: 'req-1', details: [] } };
}

describe('fetchPage', () => {
    it('sends filters, limit and cursor', async () => {
        let seen: URL | undefined;
        server.use(pagedHandler((url) => (seen = url)));
        await fetchPage(client, '/things', { query: { board_id: 'b1' }, limit: 2, cursor: 'c2' });
        expect(seen?.searchParams.get('board_id')).toBe('b1');
        expect(seen?.searchParams.get('limit')).toBe('2');
        expect(seen?.searchParams.get('cursor')).toBe('c2');
    });

    it('clamps the limit to 1..100 and omits it when not given', async () => {
        const limits: (string | null)[] = [];
        server.use(pagedHandler((url) => limits.push(url.searchParams.get('limit'))));
        await fetchPage(client, '/things', { limit: 500 });
        await fetchPage(client, '/things', { limit: 0 });
        await fetchPage(client, '/things', {});
        expect(limits).toEqual(['100', '1', null]);
    });

    it('rejects a list that is not in the page format', async () => {
        server.use(http.get(`${API}/things`, () => HttpResponse.json({ items: 'nope' })));
        await expect(fetchPage(client, '/things')).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
    });
});

describe('PageLoader', () => {
    it('loads page after page until next_cursor is null, then stops', async () => {
        let requests = 0;
        server.use(pagedHandler(() => (requests += 1)));
        const loader = new PageLoader<{ id: string }>(client, { path: '/things', limit: 2 });

        expect(loader.hasMore).toBe(true);
        await loader.next();
        expect(loader.items.map((i) => i.id)).toEqual(['a', 'b']);
        await loader.next();
        await loader.next();
        expect(loader.items.map((i) => i.id)).toEqual(['a', 'b', 'c', 'd', 'e']);
        expect(loader.hasMore).toBe(false);
        expect(loader.status).toBe('ready');

        await loader.next();
        expect(requests).toBe(3);
    });

    it('keeps one entry per id and uses the newer copy', async () => {
        server.use(
            http.get(`${API}/things`, ({ request }) => {
                const cursor = new URL(request.url).searchParams.get('cursor');
                if (cursor === null) {
                    return HttpResponse.json({ items: [{ id: 'a', v: 1 }, { id: 'b', v: 1 }], next_cursor: 'c1' });
                }
                return HttpResponse.json({ items: [{ id: 'b', v: 2 }, { id: 'c', v: 1 }], next_cursor: null });
            }),
        );
        const loader = new PageLoader<{ id: string; v: number }>(client, { path: '/things' });
        await loader.next();
        await loader.next();
        expect(loader.items).toEqual([
            { id: 'a', v: 1 },
            { id: 'b', v: 2 },
            { id: 'c', v: 1 },
        ]);
    });

    it('shares one request between concurrent next() calls', async () => {
        let requests = 0;
        server.use(pagedHandler(() => (requests += 1)));
        const loader = new PageLoader<{ id: string }>(client, { path: '/things', limit: 2 });
        await Promise.all([loader.next(), loader.next()]);
        expect(requests).toBe(1);
        expect(loader.items).toHaveLength(2);
    });

    it('exposes an error without throwing, keeps loaded items, and retries the same page', async () => {
        let failNext = false;
        server.use(
            http.get(`${API}/things`, ({ request }) => {
                const url = new URL(request.url);
                if (failNext && url.searchParams.get('cursor') !== null) {
                    return HttpResponse.json(errorBody('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                }
                const start = Number((url.searchParams.get('cursor') ?? 'c0').slice(1));
                return HttpResponse.json({
                    items: DATA.slice(start, start + 2),
                    next_cursor: start + 2 < DATA.length ? `c${start + 2}` : null,
                });
            }),
        );
        const loader = new PageLoader<{ id: string }>(client, { path: '/things', limit: 2 });
        await loader.next();

        failNext = true;
        await loader.next();
        expect(loader.status).toBe('error');
        expect(loader.error?.code).toBe('DEPENDENCY_UNAVAILABLE');
        expect(loader.items.map((i) => i.id)).toEqual(['a', 'b']);

        failNext = false;
        await loader.next();
        expect(loader.status).toBe('ready');
        expect(loader.items.map((i) => i.id)).toEqual(['a', 'b', 'c', 'd']);
    });

    it('recovers from INVALID_CURSOR with restart()', async () => {
        server.use(pagedHandler());
        const loader = new PageLoader<{ id: string }>(client, { path: '/things', limit: 2 });
        await loader.next();

        server.use(
            http.get(`${API}/things`, ({ request }) => {
                if (new URL(request.url).searchParams.has('cursor')) {
                    return HttpResponse.json(errorBody('INVALID_CURSOR'), { status: 400 });
                }
                return HttpResponse.json({ items: DATA.slice(0, 2), next_cursor: 'c2' });
            }),
        );
        await loader.next();
        expect(loader.error?.code).toBe('INVALID_CURSOR');

        await loader.restart();
        expect(loader.status).toBe('ready');
        expect(loader.error).toBeUndefined();
        expect(loader.items.map((i) => i.id)).toEqual(['a', 'b']);
    });

    it('discards a stale response that arrives after restart()', async () => {
        let release!: () => void;
        const gate = new Promise<void>((resolve) => {
            release = resolve;
        });
        let signalStarted!: () => void;
        const started = new Promise<void>((resolve) => {
            signalStarted = resolve;
        });
        let calls = 0;
        server.use(
            http.get(`${API}/things`, async () => {
                calls += 1;
                if (calls === 1) {
                    signalStarted();
                    await gate;
                    return HttpResponse.json({ items: [{ id: 'old' }], next_cursor: 'c1' });
                }
                return HttpResponse.json({ items: [{ id: 'new' }], next_cursor: null });
            }),
        );

        const loader = new PageLoader<{ id: string }>(client, { path: '/things' });
        const first = loader.next();
        await started;
        await loader.restart();
        release();
        await first;

        expect(loader.items).toEqual([{ id: 'new' }]);
        expect(loader.hasMore).toBe(false);
    });
});