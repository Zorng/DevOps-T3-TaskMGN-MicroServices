/** Error codes from api_contracts/conventions.md. Unknown codes are still accepted. */
export const KNOWN_ERROR_CODES = [
    'MALFORMED_REQUEST',
    'INVALID_CURSOR',
    'IDEMPOTENCY_KEY_REQUIRED',
    'AUTH_REQUIRED',
    'SESSION_INVALID',
    'GOOGLE_TOKEN_INVALID',
    'AUTH_CHALLENGE_INVALID',
    'SERVICE_CREDENTIAL_INVALID',
    'FORBIDDEN',
    'ORIGIN_NOT_ALLOWED',
    'NOT_FOUND',
    'USER_NOT_FOUND',
    'WORKSPACE_LIMIT_REACHED',
    'BOARD_LIMIT_REACHED',
    'MEMBER_LIMIT_REACHED',
    'ALREADY_MEMBER',
    'INVITATION_ALREADY_PENDING',
    'INVITATION_NOT_PENDING',
    'OWNER_CANNOT_LEAVE',
    'IDEMPOTENCY_CONFLICT',
    'IDEMPOTENCY_IN_PROGRESS',
    'EMAIL_AMBIGUOUS',
    'VERSION_CONFLICT',
    'PAYLOAD_TOO_LARGE',
    'UNSUPPORTED_MEDIA_TYPE',
    'VALIDATION_FAILED',
    'PRECONDITION_REQUIRED',
    'RATE_LIMITED',
    'INTERNAL_ERROR',
    'DEPENDENCY_UNAVAILABLE',
] as const;

export type KnownErrorCode = (typeof KNOWN_ERROR_CODES)[number];

/** Client-side codes for failures that never produced a contract error body. */
export type ClientErrorCode = 'NETWORK_ERROR' | 'INVALID_RESPONSE';

// `(string & {})` keeps autocomplete for known codes while still accepting new server codes.
export type ApiErrorCode = KnownErrorCode | ClientErrorCode | (string & {});

export interface ApiErrorDetail {
    field: string;
    reason: string;
}

export interface ParsedErrorBody {
    code: string;
    message: string;
    requestId: string | undefined;
    details: ApiErrorDetail[];
}

export interface ApiErrorInit {
    status: number;
    code: ApiErrorCode;
    message: string;
    requestId?: string | undefined;
    details?: ApiErrorDetail[] | undefined;
    retryAfterSeconds?: number | undefined;
}

export class ApiError extends Error {
    /** HTTP status, or 0 when no response was received. */
    readonly status: number;
    readonly code: ApiErrorCode;
    readonly requestId: string | undefined;
    readonly details: ApiErrorDetail[];
    readonly retryAfterSeconds: number | undefined;

    constructor(init: ApiErrorInit) {
        super(init.message);
        this.name = 'ApiError';
        this.status = init.status;
        this.code = init.code;
        this.requestId = init.requestId;
        this.details = init.details ?? [];
        this.retryAfterSeconds = init.retryAfterSeconds;
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Validates an untrusted JSON value as the contract's error envelope. Returns null if it isn't one. */
export function parseErrorBody(body: unknown): ParsedErrorBody | null {
    if (!isRecord(body)) return null;
    const error = body['error'];
    if (!isRecord(error)) return null;

    const code = error['code'];
    const message = error['message'];
    if (typeof code !== 'string' || code === '' || typeof message !== 'string') return null;

    const requestId = error['request_id'];
    const rawDetails = error['details'];
    const details: ApiErrorDetail[] = [];
    if (Array.isArray(rawDetails)) {
    for (const item of rawDetails) {
        if (isRecord(item) && typeof item['field'] === 'string' && typeof item['reason'] === 'string') {
            details.push({ field: item['field'], reason: item['reason'] });
        }
    }
    }

    return {
        code,
        message,
        requestId: typeof requestId === 'string' ? requestId : undefined,
        details,
    };
}

/** Retry-After is whole seconds per the contract. Anything else is ignored. */
export function parseRetryAfter(value: string | null): number | undefined {
    if (value === null || !/^\d+$/.test(value.trim())) return undefined;
    return Number.parseInt(value.trim(), 10);
}

/** Builds an ApiError from a non-OK fetch Response, tolerating non-contract bodies (e.g. a proxy's HTML page). */
export async function apiErrorFromResponse(response: Response): Promise<ApiError> {
    let body: unknown;
    try {
    body = await response.json();
    } catch {
        body = undefined;
    }
    const parsed = parseErrorBody(body);
    const retryAfterSeconds = parseRetryAfter(response.headers.get('Retry-After'));
    const headerRequestId = response.headers.get('X-Request-ID') ?? undefined;

    if (parsed) {
        return new ApiError({
            status: response.status,
            code: parsed.code,
            message: parsed.message,
            requestId: parsed.requestId ?? headerRequestId,
            details: parsed.details,
            retryAfterSeconds,
        });
    };
    return new ApiError({
        status: response.status,
        code: 'INVALID_RESPONSE',
        message: `Unexpected error response (HTTP ${response.status}).`,
        requestId: headerRequestId,
        retryAfterSeconds,
    });
}

/** The request produced no response (offline, DNS, CORS failure, connection reset). */
export function networkApiError(): ApiError {
    return new ApiError({
        status: 0,
        code: 'NETWORK_ERROR',
        message: 'The service could not be reached.',
    });
}

/** True for 404 NOT_FOUND: the resource is missing, deleted, or hidden from this user. */
export function isNotFound(error: unknown): boolean {
    return error instanceof ApiError && error.code === 'NOT_FOUND';
}