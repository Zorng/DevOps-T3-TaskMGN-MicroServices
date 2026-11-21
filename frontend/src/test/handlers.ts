import { http, HttpResponse } from 'msw/http';

export const TEST_API_BASE_URL = 'http://localhost:8080';

export const handlers = [
    http.get(`${TEST_API_BASE_URL}/api/v1/me`, () =>
        HttpResponse.json(
            {
                error: {
                    code: 'AUTH_REQUIRED',
                    message: 'Authentication is required.',
                    request_id: '00000000-0000-4000-8000-000000000001',
                    details: [],
                },
            },
            { status: 401 },
        ),
    ),
];