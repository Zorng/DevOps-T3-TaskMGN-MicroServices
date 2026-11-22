import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { TEST_API_BASE_URL } from './handlers';
import { server } from './server';

describe('mocked gateway', () => {
    it('returns the default contract error envelope', async () => {
        const response = await fetch(`${TEST_API_BASE_URL}/api/v1/me`);
        expect(response.status).toBe(401);
        const body = (await response.json()) as { error: { code: string; details: unknown[] } };
        expect(body.error.code).toBe('AUTH_REQUIRED');
        expect(body.error.details).toEqual([]);
    });

    it('allows a test to override a response', async () => {
        server.use(
            http.get(`${TEST_API_BASE_URL}/api/v1/me`, () =>
                HttpResponse.json(
                    { error: { code: 'DEPENDENCY_UNAVAILABLE', message: 'x', request_id: 'r', details: [] } },
                    { status: 503, headers: { 'Retry-After': '5' } },
                ),
            ),
        );
        const response = await fetch(`${TEST_API_BASE_URL}/api/v1/me`);
        expect(response.status).toBe(503);
        expect(response.headers.get('Retry-After')).toBe('5');
    });

    it('fails requests that have no mock handler', async () => {
        await expect(fetch(`${TEST_API_BASE_URL}/api/v1/unmocked`)).rejects.toThrow();
    });
});