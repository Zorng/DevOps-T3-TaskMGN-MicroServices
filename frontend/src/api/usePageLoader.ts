import { useCallback, useEffect, useMemo, useReducer } from 'react';
import type { ApiClient } from './client';
import { PageLoader, type PageLoaderOptions } from './pagination';

/**
 * Loads a paginated list for a screen. The first page loads automatically.
 * loadMore() loads the next page, or retries the page that failed.
 * reload() starts over from the first page.
 */
export function usePageLoader<T extends { id: string }>(client: ApiClient, options: PageLoaderOptions) {
    const { path, limit } = options;
    // A cursor only works with the filters that issued it, so new filters mean a new loader.
    const queryKey = JSON.stringify(options.query ?? {});
    const loader = useMemo(
        () =>
        new PageLoader<T>(client, {
            path,
            limit,
            query: JSON.parse(queryKey) as PageLoaderOptions['query'],
        }),
        [client, path, limit, queryKey],
    );
    const [, rerender] = useReducer((n: number) => n + 1, 0);

    useEffect(() => {
        let active = true;
        const done = () => {
        if (active) rerender();
        };
        void loader.next().then(done, done);
        rerender(); // show the loading state right away
        return () => {
        active = false;
        };
    }, [loader]);

    const loadMore = useCallback(() => {
        void loader.next().then(rerender, rerender);
        rerender();
    }, [loader]);

    const reload = useCallback(() => {
        void loader.restart().then(rerender, rerender);
        rerender();
    }, [loader]);

    return {
        items: loader.items,
        status: loader.status,
        error: loader.error,
        hasMore: loader.hasMore,
        loadMore,
        reload,
    };
}