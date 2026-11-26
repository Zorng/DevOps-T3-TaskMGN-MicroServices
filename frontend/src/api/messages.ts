import { ApiError } from './errors';

export const GENERIC_ERROR_MESSAGE = 'Something went wrong. Please try again.';

/** Turns an error into text for the user, chosen by code (never the server's own message). */
export function describeError(error: unknown, fallback: string = GENERIC_ERROR_MESSAGE): string {
    if (!(error instanceof ApiError)) return fallback;

    switch (error.code) {
        case 'NETWORK_ERROR':
        return 'The service could not be reached. Check your connection and try again.';
        case 'ORIGIN_NOT_ALLOWED':
        return 'This site address is not allowed to use the service yet. Ask the team to add it to the allowed origins.';
        case 'AUTH_CHALLENGE_INVALID':
        return 'The sign-in attempt expired. Please try again.';
        case 'GOOGLE_TOKEN_INVALID':
        return 'Google sign-in could not be verified. Please try again.';
        case 'AUTH_REQUIRED':
        case 'SESSION_INVALID':
        return 'Your session has ended. Please sign in again.';
        case 'RATE_LIMITED':
        return error.retryAfterSeconds === undefined
            ? 'Too many attempts. Please wait a moment and try again.'
            : `Too many attempts. Please wait ${error.retryAfterSeconds} seconds and try again.`;
        case 'DEPENDENCY_UNAVAILABLE':
        return error.retryAfterSeconds === undefined
            ? 'The service is temporarily unavailable. Please try again shortly.'
            : `The service is temporarily unavailable. Please try again in about ${error.retryAfterSeconds} seconds.`;
        case 'INTERNAL_ERROR':
        return 'The service ran into a problem. Please try again.';
        default:
        return fallback;
    }
}