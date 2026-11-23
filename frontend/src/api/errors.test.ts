import { describe, expect, it } from "vitest";
import { ApiError, apiErrorFromResponse, networkApiError, parseErrorBody, parseRetryAfter } from "./errors";

const envelope = {
    error: {
        code: 'VALIDATION_FAILED',
        message: 'The request contains invalid fields.',
        request_id: '00000000-0000-4000-8000-000000000012',
        details: [{ field: 'due_date', reason: 'Must be on or after start_date.' }],
    },
};

function jsonResponse(body: unknown, status: number, headers: Record<string, string> = {}) {
    return new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json', ...headers },
    });
}

describe('parseErrorBody', () => {
    it('reads the contract error envelope', () => {
        expect(parseErrorBody(envelope)).toEqual({
            code: 'VALIDATION_FAILED',
            message: 'The request contains invalid fields.',
            requestId: '00000000-0000-4000-8000-000000000012',
            details: [{ field: 'due_date', reason: 'Must be on or after start_date.' }],
        });
    });

    it.each([null, undefined, 'text', 42, [], {}, { error: 'x' }, { error: { message: 'no code' } }])(
        'rejects %j',
        (value) => {
            expect(parseErrorBody(value)).toBeNull();
        },
    );

    it('drops malformed detail entries and treats missing details as empty', () => {
        const parsed = parseErrorBody({
            error: { code: 'X', message: 'm', details: [{ field: 'a', reason: 'b' }, { field: 1 }, 'z'] },
        });
        expect(parsed?.details).toEqual([{ field: 'a', reason: 'b' }]);
        expect(parseErrorBody({ error: { code: 'X', message: 'm' } })?.details).toEqual([]);
    });
});

describe('parseRetryAfter', () => {
    it('accepts whole seconds only', () => {
        expect(parseRetryAfter('5')).toBe(5);
        expect(parseRetryAfter(' 30 ')).toBe(30);
        expect(parseRetryAfter(null)).toBeUndefined();
        expect(parseRetryAfter('soon')).toBeUndefined();
        expect(parseRetryAfter('-1')).toBeUndefined();
    });
});

describe('apiErrorFromResponse', () => {
    it('maps a contract error with details and request ID', async () => {
        const error = await apiErrorFromResponse(jsonResponse(envelope, 422));
        expect(error).toBeInstanceOf(ApiError);
        expect(error).toBeInstanceOf(Error);
        expect(error.name).toBe('ApiError');
        expect(error.status).toBe(422);
        expect(error.code).toBe('VALIDATION_FAILED');
        expect(error.requestId).toBe('00000000-0000-4000-8000-000000000012');
        expect(error.details).toHaveLength(1);
    });

    it('keeps Retry-After from a 503', async () => {
        const body = { error: { code: 'DEPENDENCY_UNAVAILABLE', message: 'm', request_id: 'r', details: [] } };
        const error = await apiErrorFromResponse(jsonResponse(body, 503, { 'Retry-After': '5' }));
        expect(error.code).toBe('DEPENDENCY_UNAVAILABLE');
        expect(error.retryAfterSeconds).toBe(5);
    });

    it('falls back to INVALID_RESPONSE for a non-contract body and keeps the request ID header', async () => {
        const response = new Response('<html>Bad gateway</html>', {
            status: 502,
            headers: { 'Content-Type': 'text/html', 'X-Request-ID': 'abc' },
        });
        const error = await apiErrorFromResponse(response);
        expect(error.status).toBe(502);
        expect(error.code).toBe('INVALID_RESPONSE');
        expect(error.requestId).toBe('abc');
        expect(error.details).toEqual([]);
    });
});

describe('networkApiError', () => {
    it('represents a request that received no response', () => {
        const error = networkApiError();
        expect(error.status).toBe(0);
        expect(error.code).toBe('NETWORK_ERROR');
    });
});