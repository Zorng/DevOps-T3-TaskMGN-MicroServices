export const OWNER_ID = '00000000-0000-4000-8000-000000000001';
export const MEMBER_ID = '00000000-0000-4000-8000-000000000002';
export const WORKSPACE_ID = '00000000-0000-4000-8000-000000000003';

export function workspaceFixture(overrides: Record<string, unknown> = {}) {
    return {
        id: WORKSPACE_ID,
        name: 'DevOps Team',
        owner_id: OWNER_ID,
        my_role: 'owner',
        member_count: 2,
        version: 2,
        created_at: '2026-11-14T10:00:00Z',
        updated_at: '2026-11-14T10:00:00Z',
        ...overrides,
    };
}

export function errorBodyFixture(code: string, details: { field: string; reason: string }[] = []) {
    return {
        error: {
            code,
            message: 'Server text that screens must not rely on.',
            request_id: '00000000-0000-4000-8000-000000000012',
            details,
        },
    };
}