import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import {
    boardFixture,
    errorBodyFixture,
    INVITATION_ID,
    invitationFixture,
    memberFixture,
    MEMBER_ID,
    notificationFixture,
    TASK_ID,
    WORKSPACE_ID,
    workspaceFixture,
} from '../test/fixtures';
import { API, challengeHandler, renderApp } from '../test/renderApp';
import { server } from '../test/server';

const listUrl = `${API}/notifications`;
const countUrl = `${API}/notifications/unread-count`;
const open = () => renderApp({ signedIn: true, path: '/notifications' });
const list = () => screen.findByRole('list', { name: 'Notifications' });

const N1 = notificationFixture();
const N2 = notificationFixture({
    id: '00000000-0000-4000-8000-000000000012',
    type: 'mention',
    actor: { id: MEMBER_ID, display_name: 'Dara' },
    comment_id: '00000000-0000-4000-8000-000000000007',
    read_at: '2026-11-14T11:00:00Z',
});
const N3 = notificationFixture({
    id: '00000000-0000-4000-8000-000000000013',
    type: 'invitation',
    invitation_id: INVITATION_ID,
    task_id: null,
});

let unread = 2;
let listCalls = 0;
let queries: string[] = [];

beforeEach(() => {
    unread = 2;
    listCalls = 0;
    queries = [];
    server.use(
        http.get(listUrl, ({ request }) => {
            listCalls += 1;
            queries.push(new URL(request.url).search);
            return HttpResponse.json({ items: [N1, N2, N3], next_cursor: null });
        }),
        http.get(countUrl, () => HttpResponse.json({ count: unread })),
    );
});

describe('notification list', () => {
    it('shows loading, then each notification with its type, sentence, state and link, and the unread count', async () => {
        server.use(
            http.get(listUrl, async () => {
                await delay(100);
                return HttpResponse.json({ items: [N1, N2, N3], next_cursor: null });
            }),
        );
        open();
        expect(await screen.findByText('Loading notifications…')).toBeInTheDocument();
        const items = within(await list()).getAllByRole('listitem');
        expect(items).toHaveLength(3);

        expect(items[0]).toHaveTextContent('Assignment');
        expect(items[0]).toHaveTextContent('Example Owner assigned you a task');
        expect(items[0]).toHaveTextContent('Unread');
        expect(within(items[0]!).getByRole('link', { name: 'Open task' })).toHaveAttribute(
            'href',
            `/workspaces/${WORKSPACE_ID}/tasks/${TASK_ID}`,
        );

        expect(items[1]).toHaveTextContent('Mention');
        expect(items[1]).toHaveTextContent('Dara mentioned you in a comment');
        expect(items[1]).not.toHaveTextContent('Unread');
        expect(within(items[1]!).queryByRole('button', { name: 'Mark as read' })).not.toBeInTheDocument();

        expect(items[2]).toHaveTextContent('Invitation');
        expect(items[2]).toHaveTextContent('invited you to join a workspace');
        expect(within(items[2]!).getByRole('link', { name: 'Open invitation' })).toHaveAttribute(
            'href',
            `/invitations/${INVITATION_ID}`,
        );

        expect(await screen.findByText('Unread: 2')).toBeInTheDocument();
    });

    it('shows an empty state', async () => {
        unread = 0;
        server.use(http.get(listUrl, () => HttpResponse.json({ items: [], next_cursor: null })));
        open();
        expect(await screen.findByRole('heading', { name: 'No notifications' })).toBeInTheDocument();
        expect(await screen.findByText('Unread: 0')).toBeInTheDocument();
    });

    it('filters to unread only, and says so when there are none', async () => {
        const user = userEvent.setup();
        open();
        await list();
        server.use(
            http.get(listUrl, ({ request }) => {
                queries.push(new URL(request.url).search);
                return HttpResponse.json({ items: [], next_cursor: null });
            }),
        );
        await user.click(screen.getByRole('checkbox', { name: 'Show unread only' }));
        expect(await screen.findByRole('heading', { name: 'No unread notifications' })).toBeInTheDocument();
        expect(queries.at(-1)).toContain('unread_only=true');
        expect(queries[0]).not.toContain('unread_only');
    });

    it('loads the next page with the cursor and keeps the first page', async () => {
        const cursors: (string | null)[] = [];
        server.use(
            http.get(listUrl, ({ request }) => {
                const cursor = new URL(request.url).searchParams.get('cursor');
                cursors.push(cursor);
                if (cursor === null) return HttpResponse.json({ items: [N1], next_cursor: 'page-2' });
                return HttpResponse.json({ items: [N3], next_cursor: null });
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.click(screen.getByRole('button', { name: 'Load more notifications' }));
        await waitFor(() => expect(within(screen.getByRole('list', { name: 'Notifications' })).getAllByRole('listitem')).toHaveLength(2));
        expect(cursors).toEqual([null, 'page-2']);
        expect(screen.queryByRole('button', { name: 'Load more notifications' })).not.toBeInTheDocument();
    });
});

describe('mark as read', () => {
    const markUrl = `${API}/notifications/${N1.id}/read`;

    it('marks one notification read and shows the server’s unread count, not a local subtraction', async () => {
        server.use(
            http.post(markUrl, () => {
                unread = 5; // the server also received new notifications meanwhile
                return HttpResponse.json({ ...N1, read_at: '2026-11-14T12:00:00Z' });
            }),
        );
        const user = userEvent.setup();
        open();
        const items = within(await list()).getAllByRole('listitem');
        await screen.findByText('Unread: 2');
        await user.click(within(items[0]!).getByRole('button', { name: 'Mark as read' }));

        await waitFor(() => expect(items[0]).not.toHaveTextContent('Unread'));
        expect(within(items[0]!).queryByRole('button', { name: 'Mark as read' })).not.toBeInTheDocument();
        expect(await screen.findByText('Unread: 5')).toBeInTheDocument();
    });

    it('sends one request for repeated clicks', async () => {
        let posts = 0;
        server.use(
            http.post(markUrl, async () => {
                posts += 1;
                await delay(100);
                unread = 1;
                return HttpResponse.json({ ...N1, read_at: '2026-11-14T12:00:00Z' });
            }),
        );
        const user = userEvent.setup();
        open();
        const items = within(await list()).getAllByRole('listitem');
        await screen.findByText('Unread: 2');
        const button = within(items[0]!).getByRole('button', { name: 'Mark as read' });
        await user.dblClick(button);
        await user.click(button);

        expect(await screen.findByText('Unread: 1')).toBeInTheDocument();
        expect(posts).toBe(1);
    });

    it('leaves the item and the count unchanged when the request fails, and can be retried', async () => {
        let posts = 0;
        server.use(
            http.post(markUrl, () => {
                posts += 1;
                if (posts === 1) return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                unread = 1;
                return HttpResponse.json({ ...N1, read_at: '2026-11-14T12:00:00Z' });
            }),
        );
        const user = userEvent.setup();
        open();
        const items = within(await list()).getAllByRole('listitem');
        await screen.findByText('Unread: 2');
        await user.click(within(items[0]!).getByRole('button', { name: 'Mark as read' }));

        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        expect(items[0]).toHaveTextContent('Unread');
        expect(screen.getByText('Unread: 2')).toBeInTheDocument();

        await user.click(within(items[0]!).getByRole('button', { name: 'Mark as read' }));
        expect(await screen.findByText('Unread: 1')).toBeInTheDocument();
        expect(posts).toBe(2);
    });

    it('re-reads the list when the notification no longer exists for me', async () => {
        server.use(http.post(markUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })));
        const user = userEvent.setup();
        open();
        const items = within(await list()).getAllByRole('listitem');
        const before = listCalls;
        await user.click(within(items[0]!).getByRole('button', { name: 'Mark as read' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('no longer available');
        await waitFor(() => expect(listCalls).toBeGreaterThan(before));
    });
});

describe('refresh and failures', () => {
    it('refreshes the list and the count on request', async () => {
        const user = userEvent.setup();
        open();
        await list();
        await screen.findByText('Unread: 2');
        const newer = notificationFixture({ id: '00000000-0000-4000-8000-000000000014', actor: { id: MEMBER_ID, display_name: 'Lisa' } });
        server.use(http.get(listUrl, () => HttpResponse.json({ items: [newer, N1], next_cursor: null })));
        unread = 3;
        await user.click(screen.getByRole('button', { name: 'Refresh' }));

        expect(await screen.findByText('Lisa')).toBeInTheDocument();
        expect(await screen.findByText('Unread: 3')).toBeInTheDocument();
    });

    it('shows an error with a retry when the list cannot be refreshed', async () => {
        const user = userEvent.setup();
        open();
        await list();
        server.use(http.get(listUrl, () => HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 })));
        await user.click(screen.getByRole('button', { name: 'Refresh' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');

        server.use(http.get(listUrl, () => HttpResponse.json({ items: [N1], next_cursor: null })));
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await list()).toBeInTheDocument();
    });

    it('keeps the last known count, marked possibly out of date, when only the count fails to refresh', async () => {
        const user = userEvent.setup();
        open();
        await list();
        await screen.findByText('Unread: 2');
        server.use(http.get(countUrl, () => HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 })));
        await user.click(screen.getByRole('button', { name: 'Refresh' }));
        expect(await screen.findByText(/Unread: 2 \(could not be refreshed/)).toBeInTheDocument();
    });

    it('says so when the count cannot be loaded at all', async () => {
        server.use(http.get(countUrl, () => HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 })));
        open();
        await list();
        expect(await screen.findByText('The unread count could not be loaded.')).toBeInTheDocument();
    });

    it('signs out and returns to sign-in when the session has expired', async () => {
        server.use(
            http.get(listUrl, () => HttpResponse.json(errorBodyFixture('SESSION_INVALID'), { status: 401 })),
            http.get(countUrl, () => HttpResponse.json(errorBodyFixture('SESSION_INVALID'), { status: 401 })),
            challengeHandler(),
        );
        const { services } = open();
        expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
        expect(services.store.getSnapshot().status).toBe('signed_out');
    });
});

describe('opening a target', () => {
    it('shows an unavailable task, because a notification grants no access', async () => {
        server.use(
            http.get(`${API}/workspaces/${WORKSPACE_ID}`, () => HttpResponse.json(workspaceFixture(), { headers: { ETag: '"2"' } })),
            http.get(`${API}/workspaces/${WORKSPACE_ID}/tasks/${TASK_ID}`, () =>
                HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 }),
            ),
            http.get(`${API}/workspaces/${WORKSPACE_ID}/boards`, () => HttpResponse.json({ items: [boardFixture()] })),
            http.get(`${API}/workspaces/${WORKSPACE_ID}/members`, () => HttpResponse.json({ items: [memberFixture()] })),
        );
        const user = userEvent.setup();
        open();
        const items = within(await list()).getAllByRole('listitem');
        await user.click(within(items[0]!).getByRole('link', { name: 'Open task' }));
        expect(await screen.findByRole('heading', { name: 'Task not available' })).toBeInTheDocument();
    });

    it('shows an unavailable invitation, and the notification is still listed afterwards', async () => {
        server.use(
            http.get(`${API}/invitations/${INVITATION_ID}`, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })),
        );
        const user = userEvent.setup();
        open();
        const items = within(await list()).getAllByRole('listitem');
        await user.click(within(items[2]!).getByRole('link', { name: 'Open invitation' }));
        expect(await screen.findByRole('heading', { name: 'Invitation not available' })).toBeInTheDocument();

        await user.click(screen.getByRole('link', { name: 'Back to notifications' }));
        const again = within(await list()).getAllByRole('listitem');
        expect(again[2]).toHaveTextContent('invited you to join a workspace');
    });

    it('opens an existing invitation', async () => {
        server.use(http.get(`${API}/invitations/${INVITATION_ID}`, () => HttpResponse.json(invitationFixture())));
        const user = userEvent.setup();
        open();
        const items = within(await list()).getAllByRole('listitem');
        await user.click(within(items[2]!).getByRole('link', { name: 'Open invitation' }));
        expect(await screen.findByRole('heading', { name: 'Invitation to DevOps Team' })).toBeInTheDocument();
    });
});
