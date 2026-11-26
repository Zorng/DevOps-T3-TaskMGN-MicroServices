import type { ApiClient, ApiResponse } from './client';
import { ApiError } from './errors';

export type AttemptState = 'idle' | 'in_flight' | 'succeeded' | 'retryable' | 'rejected';

/**
 * The server may or may not have applied the change: no response, a 5xx, or an identical
 * request that is still being processed. Safe recovery for a create is to retry with the same key.
 */
export function isAmbiguousOutcome(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return error.status === 0 || error.status >= 500 || error.code === 'IDEMPOTENCY_IN_PROGRESS';
}

/** Ambiguous outcomes plus rate limiting (wait for retryAfterSeconds first). */
export function canRetryWithSameKey(error: unknown): boolean {
  return isAmbiguousOutcome(error) || (error instanceof ApiError && error.status === 429);
}

/** 412: the resource changed since it was read. Reload it and ask the user to reconcile. */
export function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'VERSION_CONFLICT';
}

/**
 * One user intention to create one resource. It owns a single Idempotency-Key for its whole life.
 * Nothing here retries automatically: the caller (a button click, or a timer that respects
 * retryAfterSeconds) calls send() again, and the same key is reused.
 * After a definitive rejection the attempt is final; create a new attempt for the corrected input.
 */
export class CreateAttempt<T> {
    readonly idempotencyKey: string;

    private readonly client: ApiClient;
    private readonly path: string;
    private readonly body: unknown;
    private currentState: AttemptState = 'idle';
    private inFlight: Promise<ApiResponse<T>> | undefined;
    private response: ApiResponse<T> | undefined;
    private failure: ApiError | undefined;

    constructor(
        client: ApiClient,
        path: string,
        body: unknown,
        newKey: () => string = () => crypto.randomUUID(),
    ) {
        this.client = client;
        this.path = path;
        this.body = body;
        this.idempotencyKey = newKey();
    }

    get state(): AttemptState {
        return this.currentState;
    }

    /** The last failure, if any. */
    get error(): ApiError | undefined {
        return this.failure;
    }

    send(options: { signal?: AbortSignal } = {}): Promise<ApiResponse<T>> {
        if (this.response) return Promise.resolve(this.response); // already created: never send twice
        if (this.inFlight) return this.inFlight; // double click: share the running request
        if (this.currentState === 'rejected' && this.failure) return Promise.reject(this.failure);

        this.currentState = 'in_flight';
        this.inFlight = this.client
            .post<T>(this.path, {
                body: this.body,
                idempotencyKey: this.idempotencyKey,
                signal: options.signal,
            })
            .then((result) => {
                this.response = result;
                this.failure = undefined;
                this.currentState = 'succeeded';
                return result;
            })
            .catch((error: unknown) => {
            if (error instanceof ApiError) {
                this.failure = error;
                this.currentState = canRetryWithSameKey(error) ? 'retryable' : 'rejected';
            } else {
                // e.g. the caller aborted: the server may still have processed it.
                this.currentState = 'retryable';
            }
            throw error;
            })
            .finally(() => {
                this.inFlight = undefined;
            });
        return this.inFlight;
    }
}

/** PATCH a versioned resource (workspace, board, task). The ETag is required, so If-Match can't be forgotten. */
export function updateWithVersion<T>(
    client: ApiClient,
    path: string,
    body: unknown,
    etag: string,
    signal?: AbortSignal,
): Promise<ApiResponse<T>> {
    return client.patch<T>(path, { body, ifMatch: etag, signal });
}

/** DELETE a versioned resource. Same rule: the ETag is required. */
export function deleteWithVersion<T = undefined>(
    client: ApiClient,
    path: string,
    etag: string,
    signal?: AbortSignal,
): Promise<ApiResponse<T>> {
    return client.delete<T>(path, { ifMatch: etag, signal });
}