import { describe, expect, it } from 'vitest';
import { ApiError } from './errors';
import { describeError, GENERIC_ERROR_MESSAGE } from './messages';

const make = (code: string, status = 400, retryAfterSeconds?: number) =>
    new ApiError({ status, code, message: 'raw server text', retryAfterSeconds });

describe('describeError', () => {
    it.each([
        ['NETWORK_ERROR', 'could not be reached'],
        ['ORIGIN_NOT_ALLOWED', 'not allowed'],
        ['AUTH_CHALLENGE_INVALID', 'expired'],
        ['GOOGLE_TOKEN_INVALID', 'could not be verified'],
        ['SESSION_INVALID', 'session has ended'],
        ['AUTH_REQUIRED', 'session has ended'],
        ['WORKSPACE_LIMIT_REACHED', 'maximum'],
        ['VALIDATION_FAILED', 'not valid'],
        ['IDEMPOTENCY_IN_PROGRESS', 'still being processed'],
        ['INTERNAL_ERROR', 'problem'],
    ])('describes %s', (code, fragment) => {
        expect(describeError(make(code))).toContain(fragment);
    });

    it('includes the wait time for rate limiting and unavailable services when known', () => {
        expect(describeError(make('RATE_LIMITED', 429, 12))).toContain('12 seconds');
        expect(describeError(make('RATE_LIMITED', 429))).toContain('a moment');
        expect(describeError(make('DEPENDENCY_UNAVAILABLE', 503, 5))).toContain('about 5 seconds');
        expect(describeError(make('DEPENDENCY_UNAVAILABLE', 503))).toContain('shortly');
    });

    it('never shows the raw server message', () => {
        expect(describeError(make('SOMETHING_NEW'))).not.toContain('raw server text');
    });

    it('uses the fallback for unknown codes and for errors that are not API errors', () => {
        expect(describeError(make('SOMETHING_NEW'))).toBe(GENERIC_ERROR_MESSAGE);
        expect(describeError(new Error('boom'), 'Custom fallback.')).toBe('Custom fallback.');
        expect(describeError(make('SOMETHING_NEW'), 'Custom fallback.')).toBe('Custom fallback.');
    });
});