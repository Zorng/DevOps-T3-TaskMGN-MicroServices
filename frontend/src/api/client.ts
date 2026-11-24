import { ApiError, networkApiError, apiErrorFromResponse } from "./errors";

const API_PREFIX = '/api/v1';

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';
type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;
type QueryValue = string | number | boolean | undefined;

export interface ApiClientOptions {
    /** Public gateway base URL, e.g. http://localhost:8080 */
    baseUrl: string;
    /** Returns the in-memory application token, or null when signed out. */
    getToken: () => string | null;
    /** Called when an authenticated request gets 401 (the session is no longer valid). */
    onUnauthorized?: (error: ApiError) => void;
    /** Override for tests; defaults to the global fetch, looked up at call time. */
    fetchImpl?: FetchLike;
    /** Override for tests; defaults to crypto.randomUUID. */
    newRequestId?: () => string;
}

export interface RequestOptions {
    query?: Record<string, QueryValue>;
    body?: unknown;
    /** Required by the contract for creates; the caller decides when to reuse a key. */
    idempotencyKey?: string;
    /** Exact ETag from the last read, quotes included, e.g. "3". */
    ifMatch?: string;
    signal?: AbortSignal;
    /** Set false for sign-in calls that have no token yet. Defaults to true. */
    auth?: boolean;
}

export interface ApiResponse<T> {
    data: T;
    status: number;
    /** ETag response header, kept for later If-Match use. */
    etag: string | undefined;
    /** Server-reported X-Request-ID, useful for support and logs. */
    requestId: string | undefined;
}

export interface ApiClient {
    get<T = unknown>(path: string, options?: RequestOptions): Promise<ApiResponse<T>>;
    post<T = unknown>(path: string, options?: RequestOptions): Promise<ApiResponse<T>>;
    patch<T = unknown>(path: string, options?: RequestOptions): Promise<ApiResponse<T>>;
    delete<T = unknown>(path: string, options?: RequestOptions): Promise<ApiResponse<T>>;
}

function buildUrl(baseUrl: string, path: string, query?: Record<string, QueryValue>): string {
    if (!path.startsWith('/')) {
        throw new Error(`API path must start with "/": ${path}`);
    }
    const url = `${baseUrl}${API_PREFIX}${path}`;
    if (!query) return url;
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
        if (value !== undefined) params.set(key, String(value));
    }
    const queryString = params.toString();
        return queryString === '' ? url : `${url}?${queryString}`;
}

function isAbortError(error: unknown): boolean {
    return error instanceof DOMException && error.name === 'AbortError';
}

export function createApiClient(options: ApiClientOptions): ApiClient {
    const baseUrl = options.baseUrl.replace(/\/+$/, '');
    const newRequestId = options.newRequestId ?? (() => crypto.randomUUID());
    const doFetch: FetchLike = options.fetchImpl ?? ((input, init) => globalThis.fetch(input, init));

    async function request<T>(
        method: Method,
        path: string,
        requestOptions: RequestOptions = {},
    ): Promise<ApiResponse<T>> {
        const url = buildUrl(baseUrl, path, requestOptions.query);
        const sendAuth = requestOptions.auth !== false;

        // Headers come from this fixed list only; callers cannot inject arbitrary headers.
        const headers = new Headers();
        headers.set('Accept', 'application/json');
        headers.set('X-Request-ID', newRequestId());
        if (sendAuth) {
        const token = options.getToken();
        if (token) headers.set('Authorization', `Bearer ${token}`);
        }
        if (requestOptions.idempotencyKey) headers.set('Idempotency-Key', requestOptions.idempotencyKey);
        if (requestOptions.ifMatch) headers.set('If-Match', requestOptions.ifMatch);

        let body: string | undefined;
        if (requestOptions.body !== undefined) {
        headers.set('Content-Type', 'application/json');
        body = JSON.stringify(requestOptions.body);
        }

        let response: Response;
        try {
        response = await doFetch(url, {
            method,
            headers,
            body,
            credentials: 'omit',
            signal: requestOptions.signal,
        });
        } catch (error) {
        if (isAbortError(error)) throw error;
        throw networkApiError();
        }

        if (!response.ok) {
        const apiError = await apiErrorFromResponse(response);
        if (response.status === 401 && sendAuth) options.onUnauthorized?.(apiError);
        throw apiError;
        }

        const requestId = response.headers.get('X-Request-ID') ?? undefined;
        const etag = response.headers.get('ETag') ?? undefined;
        const text = await response.text();
        let data: unknown;
        if (text !== '') {
        try {
            data = JSON.parse(text);
        } catch {
            throw new ApiError({
            status: response.status,
            code: 'INVALID_RESPONSE',
            message: 'The service returned a response that is not valid JSON.',
            requestId,
            });
        }
        }
        return { data: data as T, status: response.status, etag, requestId };
    }

    return {
        get: <T>(path: string, o?: RequestOptions) => request<T>('GET', path, o),
        post: <T>(path: string, o?: RequestOptions) => request<T>('POST', path, o),
        patch: <T>(path: string, o?: RequestOptions) => request<T>('PATCH', path, o),
        delete: <T>(path: string, o?: RequestOptions) => request<T>('DELETE', path, o),
    };
}