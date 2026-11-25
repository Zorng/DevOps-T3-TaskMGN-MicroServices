import { ApiError } from './errors';

export function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** A non-empty string, or null. */
export function str(value: unknown): string | null {
    return typeof value === 'string' && value !== '' ? value : null;
}

/** An integer of at least 1, or null. */
export function positiveInt(value: unknown): number | null {
    return typeof value === 'number' && Number.isInteger(value) && value >= 1 ? value : null;
}

export function invalidResponse(status: number, requestId: string | undefined): ApiError {
    return new ApiError({
        status,
        code: 'INVALID_RESPONSE',
        message: 'The service returned an unexpected response.',
        requestId,
    });
}