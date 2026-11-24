import type { ApiClient, QueryValue } from './client';
import { ApiError } from './errors';

export const MAX_PAGE_LIMIT = 100;

/** Shape of every paginated list response in the contract. */
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface FetchPageOptions {
    /** Filters such as board_id, status, unread_only. */
    query?: Record<string, QueryValue>;
    /** 1..100. Omit to use the server default (20). */
    limit?: number;
    /** An opaque cursor returned by a previous page. Never build one yourself. */
    cursor?: string | null;
    signal?: AbortSignal;
}

function clampLimit(limit: number | undefined): number | undefined {
    if (limit === undefined || !Number.isFinite(limit)) return undefined;
    return Math.min(MAX_PAGE_LIMIT, Math.max(1, Math.trunc(limit)));
}

function isPageShape(value: unknown): value is { items: unknown[]; next_cursor: string | null } {
    if (typeof value !== 'object' || value === null) return false;
    const record = value as Record<string, unknown>;
    const cursor = record['next_cursor'];
    return Array.isArray(record['items']) && (cursor === null || typeof cursor === 'string');
}

/** Fetches one page and validates its envelope. */
export async function fetchPage<T>(
    client: ApiClient,
    path: string,
    options: FetchPageOptions = {},
): Promise<Page<T>> {
    const result = await client.get<unknown>(path, {
        query: { ...options.query, limit: clampLimit(options.limit), cursor: options.cursor ?? undefined },
        signal: options.signal,
    });
    if (!isPageShape(result.data)) {
        throw new ApiError({
        status: result.status,
        code: 'INVALID_RESPONSE',
        message: 'The service returned a list in an unexpected format.',
        requestId: result.requestId,
        });
    }
    return { items: result.data.items as T[], next_cursor: result.data.next_cursor };
}

export type LoaderStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface PageLoaderOptions {
    path: string;
    query?: Record<string, QueryValue>;
    limit?: number;
}

/**
 * Loads one list page by page for a "Load more" screen. One loader = one path + one set of filters;
 * when a filter changes, create a new loader (a cursor is only valid for the scope that issued it).
 *
 * next() never rejects: read `status` and `error` instead, so a screen can render ErrorState.
 * A failed next() keeps the loaded items and the same cursor, so calling next() again retries that page.
 */
export class PageLoader<T extends { id: string }> {
    private readonly client: ApiClient;
    private readonly options: PageLoaderOptions;
    private entries: T[] = [];
    private cursor: string | null = null;
    private started = false;
    private generation = 0;
    private running: Promise<void> | undefined;
    private currentStatus: LoaderStatus = 'idle';
    private failure: ApiError | undefined;

    constructor(client: ApiClient, options: PageLoaderOptions) {
        this.client = client;
        this.options = options;
    }

    get items(): readonly T[] {
        return this.entries;
    }

    get status(): LoaderStatus {
        return this.currentStatus;
    }

    get error(): ApiError | undefined {
        return this.failure;
    }

    /** True until a page comes back with next_cursor null. */
    get hasMore(): boolean {
        return !this.started || this.cursor !== null;
    }

    /** Loads the next page (the first page if nothing is loaded yet). */
    next(): Promise<void> {
        if (this.running) return this.running; // already loading: share it
        if (!this.hasMore) return Promise.resolve();

        const generation = this.generation;
        this.currentStatus = 'loading';
        this.failure = undefined;

        this.running = fetchPage<T>(this.client, this.options.path, {
        query: this.options.query,
        limit: this.options.limit,
        cursor: this.cursor,
        })
        .then((page) => {
            if (generation !== this.generation) return; // restarted meanwhile: discard stale data
            this.append(page.items);
            this.cursor = page.next_cursor;
            this.started = true;
            this.currentStatus = 'ready';
        })
        .catch((error: unknown) => {
            if (generation !== this.generation) return;
            if (!(error instanceof ApiError)) throw error;
            this.failure = error;
            this.currentStatus = 'error';
        })
        .finally(() => {
            if (generation === this.generation) this.running = undefined;
        });
        return this.running;
    }

    /** Discards everything and loads the first page again (e.g. after INVALID_CURSOR, or a manual refresh). */
    restart(): Promise<void> {
        this.generation += 1;
        this.entries = [];
        this.cursor = null;
        this.started = false;
        this.running = undefined;
        this.failure = undefined;
        this.currentStatus = 'idle';
        return this.next();
    }

    /** Pages aren't a snapshot, so an id may repeat: keep one entry and use the newer copy. */
    private append(incoming: T[]): void {
        const indexById = new Map(this.entries.map((entry, index) => [entry.id, index] as const));
        for (const item of incoming) {
        const existing = indexById.get(item.id);
        if (existing === undefined) {
            indexById.set(item.id, this.entries.length);
            this.entries.push(item);
        } else {
            this.entries[existing] = item;
        }
        }
    }
}