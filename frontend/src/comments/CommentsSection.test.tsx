import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import {
    BOARD_ID,
    boardFixture,
    commentFixture,
    errorBodyFixture,
    memberFixture,
    MEMBER_ID,
    OWNER_ID,
    TASK_ID,
    taskFixture,
    WORKSPACE_ID,
    workspaceFixture,
} from '../test/fixtures';
import { API, renderApp } from '../test/renderApp';
import { server } from '../test/server';

const wsUrl = `${API}/workspaces/${WORKSPACE_ID}`;
const taskUrl = `${wsUrl}/tasks/${TASK_ID}`;
const commentsUrl = `${taskUrl}/comments`;
const OWNER_ROW = memberFixture({
    membership_id: '00000000-0000-4000-8000-000000000011',
    user: { id: OWNER_ID, display_name: 'Example Owner' },
    role: 'owner',
});
const DARA_ROW = memberFixture();
const open = () => renderApp({ signedIn: true, path: `/workspaces/${WORKSPACE_ID}/tasks/${TASK_ID}` });
const box = () => screen.getByLabelText('Comment');
const commentForm = () => screen.getByRole('form', { name: 'Add a comment' });
const list = () => screen.findByRole('list', { name: 'Comments' });
const send = (user: ReturnType<typeof userEvent.setup>) =>
    user.click(within(commentForm()).getByRole('button', { name: 'Add comment' }));

let memberReads = 0;
let memberRows: unknown[] = [];

beforeEach(() => {
    memberReads = 0;
    memberRows = [OWNER_ROW, DARA_ROW];
    server.use(
        http.get(wsUrl, () => HttpResponse.json(workspaceFixture({ my_role: 'member' }), { headers: { ETag: '"2"' } })),
        http.get(taskUrl, () => HttpResponse.json(taskFixture(), { headers: { ETag: '"1"' } })),
        http.get(`${wsUrl}/boards`, () => HttpResponse.json({ items: [boardFixture()] })),
        http.get(`${wsUrl}/members`, () => {
            memberReads += 1;
            return HttpResponse.json({ items: memberRows });
        }),
        http.get(commentsUrl, () => HttpResponse.json({ items: [], next_cursor: null })),
        // After a task is deleted, its board page needs these.
        http.get(`${wsUrl}/boards/${BOARD_ID}`, () => HttpResponse.json(boardFixture(), { headers: { ETag: '"1"' } })),
        http.get(`${wsUrl}/tasks`, () => HttpResponse.json({ items: [], next_cursor: null })),
    );
});

/** Opens the task page and waits until the comment form is there. */
async function ready() {
    const user = userEvent.setup();
    open();
    await screen.findByRole('form', { name: 'Add a comment' });
    await waitFor(() => expect(within(commentForm()).getAllByRole('option')).toHaveLength(3));
    return user;
}

describe('comment list', () => {
    it('shows loading, then comments in server order, as plain text, with a highlighted mention', async () => {
        server.use(
            http.get(commentsUrl, async () => {
                await delay(100);
                return HttpResponse.json({
                    items: [
                        // Offsets count code points: the emoji is one, so "@Dara" starts at 16.
                        commentFixture({
                            body: '😀 Please review @Dara',
                            mentions: [{ user: { id: MEMBER_ID, display_name: 'Dara' }, start: 16, length: 5 }],
                        }),
                        commentFixture({
                            id: '00000000-0000-4000-8000-000000000030',
                            body: '<img src=x onerror=alert(1)> & <b>bold</b>',
                            mentions: [],
                        }),
                    ],
                    next_cursor: null,
                });
            }),
        );
        open();
        expect(await screen.findByText('Loading comments…')).toBeInTheDocument();
        const items = within(await list()).getAllByRole('listitem');
        expect(items).toHaveLength(2);
        expect(items[0]).toHaveTextContent('Example Owner');
        expect(items[0]).toHaveTextContent('😀 Please review @Dara');
        expect(within(items[0]!).getByText('@Dara').tagName).toBe('STRONG');
        expect(items[1]).toHaveTextContent('<img src=x onerror=alert(1)> & <b>bold</b>');
        expect(items[1]!.querySelector('img')).toBeNull();
        expect(items[1]!.querySelector('b')).toBeNull();
    });

    it('does not treat ordinary @text in a stored comment as a mention', async () => {
        server.use(
            http.get(commentsUrl, () =>
                HttpResponse.json({ items: [commentFixture({ body: 'ping @Dara', mentions: [] })], next_cursor: null }),
            ),
        );
        open();
        const item = within(await list()).getAllByRole('listitem')[0]!;
        expect(within(item).getByText('ping @Dara').tagName).toBe('SPAN');
        expect(within(item).queryByText('@Dara')).not.toBeInTheDocument();
    });

    it('has no edit or delete controls on comments', async () => {
        server.use(http.get(commentsUrl, () => HttpResponse.json({ items: [commentFixture()], next_cursor: null })));
        open();
        const items = within(await list()).getAllByRole('listitem');
        expect(within(items[0]!).queryAllByRole('button')).toHaveLength(0);
    });

    it('shows an empty state', async () => {
        open();
        expect(await screen.findByRole('heading', { name: 'No comments yet' })).toBeInTheDocument();
    });

    it('shows an error with a retry', async () => {
        let calls = 0;
        server.use(
            http.get(commentsUrl, () => {
                calls += 1;
                if (calls === 1) return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                return HttpResponse.json({ items: [commentFixture()], next_cursor: null });
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByText(/temporarily unavailable/)).toBeInTheDocument();
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await list()).toBeInTheDocument();
    });

    it('loads the next page with the cursor and keeps the order', async () => {
        const cursors: (string | null)[] = [];
        server.use(
            http.get(commentsUrl, ({ request }) => {
                const cursor = new URL(request.url).searchParams.get('cursor');
                cursors.push(cursor);
                if (cursor === null) {
                    return HttpResponse.json({ items: [commentFixture({ body: 'first', mentions: [] })], next_cursor: 'c2' });
                }
                return HttpResponse.json({
                    items: [commentFixture({ id: '00000000-0000-4000-8000-000000000031', body: 'second', mentions: [] })],
                    next_cursor: null,
                });
            }),
        );
        const user = userEvent.setup();
        open();
        await list();
        await user.click(screen.getByRole('button', { name: 'Load more comments' }));
        await waitFor(() => expect(within(screen.getByRole('list', { name: 'Comments' })).getAllByRole('listitem')).toHaveLength(2));
        const texts = within(screen.getByRole('list', { name: 'Comments' }))
            .getAllByRole('listitem')
            .map((item) => item.textContent ?? '');
        expect(texts[0]).toContain('first');
        expect(texts[1]).toContain('second');
        expect(cursors).toEqual([null, 'c2']);
    });
});

describe('adding a comment', () => {
    it('sends the body exactly as typed with an Idempotency-Key, then reads the list again', async () => {
        const seen: { key: string | null; body: unknown }[] = [];
        let added = false;
        server.use(
            http.get(commentsUrl, () =>
                HttpResponse.json({ items: added ? [commentFixture({ body: '  Hello  ', mentions: [] })] : [], next_cursor: null }),
            ),
            http.post(commentsUrl, async ({ request }) => {
                seen.push({ key: request.headers.get('idempotency-key'), body: await request.json() });
                added = true;
                return HttpResponse.json(commentFixture({ body: '  Hello  ', mentions: [] }), { status: 201 });
            }),
        );
        const user = await ready();
        await user.type(box(), '  Hello  ');
        await send(user);

        expect(await screen.findByText('Comment added.')).toBeInTheDocument();
        expect(within(await list()).getAllByRole('listitem')).toHaveLength(1);
        expect(box()).toHaveValue('');
        expect(seen).toHaveLength(1);
        expect(seen[0]?.key).toBeTruthy();
        expect(seen[0]?.body).toEqual({ body: '  Hello  ', mentions: [] });
    });

    it('sends a picked mention with code point offsets after an emoji', async () => {
        let body: unknown;
        server.use(
            http.post(commentsUrl, async ({ request }) => {
                body = await request.json();
                return HttpResponse.json(commentFixture(), { status: 201 });
            }),
        );
        const user = await ready();
        fireEvent.change(box(), { target: { value: '😀 Hi ' } });
        await user.selectOptions(screen.getByLabelText('Mention a member'), 'Dara');
        await user.click(screen.getByRole('button', { name: 'Insert mention' }));

        expect(box()).toHaveValue('😀 Hi @Dara ');
        const chips = screen.getByRole('list', { name: 'Mentions in this comment' });
        expect(chips).toHaveTextContent('Dara');
        await send(user);

        await screen.findByText('Comment added.');
        expect(body).toEqual({ body: '😀 Hi @Dara ', mentions: [{ user_id: MEMBER_ID, start: 5, length: 5 }] });
    });

    it('does not make typed @text a mention', async () => {
        let body: unknown;
        server.use(
            http.post(commentsUrl, async ({ request }) => {
                body = await request.json();
                return HttpResponse.json(commentFixture({ mentions: [] }), { status: 201 });
            }),
        );
        const user = await ready();
        await user.type(box(), 'Hello @Dara');
        expect(screen.queryByRole('list', { name: 'Mentions in this comment' })).not.toBeInTheDocument();
        await send(user);
        await screen.findByText('Comment added.');
        expect(body).toEqual({ body: 'Hello @Dara', mentions: [] });
    });

    it('moves the mention range when text is typed before it', async () => {
        let body: unknown;
        server.use(
            http.post(commentsUrl, async ({ request }) => {
                body = await request.json();
                return HttpResponse.json(commentFixture(), { status: 201 });
            }),
        );
        const user = await ready();
        fireEvent.change(box(), { target: { value: '😀 Hi ' } });
        await user.selectOptions(screen.getByLabelText('Mention a member'), 'Dara');
        await user.click(screen.getByRole('button', { name: 'Insert mention' }));
        fireEvent.change(box(), { target: { value: 'Oh 😀 Hi @Dara ' } });
        await send(user);
        await screen.findByText('Comment added.');
        expect(body).toEqual({ body: 'Oh 😀 Hi @Dara ', mentions: [{ user_id: MEMBER_ID, start: 8, length: 5 }] });
    });

    it('turns an edited mention back into ordinary text', async () => {
        let body: unknown;
        server.use(
            http.post(commentsUrl, async ({ request }) => {
                body = await request.json();
                return HttpResponse.json(commentFixture({ mentions: [] }), { status: 201 });
            }),
        );
        const user = await ready();
        fireEvent.change(box(), { target: { value: 'Hi ' } });
        await user.selectOptions(screen.getByLabelText('Mention a member'), 'Dara');
        await user.click(screen.getByRole('button', { name: 'Insert mention' }));
        expect(screen.getByRole('list', { name: 'Mentions in this comment' })).toBeInTheDocument();

        fireEvent.change(box(), { target: { value: 'Hi @Dxra ' } });
        expect(screen.queryByRole('list', { name: 'Mentions in this comment' })).not.toBeInTheDocument();
        await send(user);
        await screen.findByText('Comment added.');
        expect(body).toEqual({ body: 'Hi @Dxra ', mentions: [] });
    });

    it('lets the user remove a mention and keeps the text', async () => {
        let body: unknown;
        server.use(
            http.post(commentsUrl, async ({ request }) => {
                body = await request.json();
                return HttpResponse.json(commentFixture({ mentions: [] }), { status: 201 });
            }),
        );
        const user = await ready();
        fireEvent.change(box(), { target: { value: 'Hi ' } });
        await user.selectOptions(screen.getByLabelText('Mention a member'), 'Dara');
        await user.click(screen.getByRole('button', { name: 'Insert mention' }));
        await user.click(screen.getByRole('button', { name: 'Remove mention of Dara' }));

        expect(box()).toHaveValue('Hi @Dara ');
        await send(user);
        await screen.findByText('Comment added.');
        expect(body).toEqual({ body: 'Hi @Dara ', mentions: [] });
    });

    it('offers only current members in the picker', async () => {
        await ready();
        const options = within(screen.getByLabelText('Mention a member')).getAllByRole('option');
        expect(options.map((option) => option.textContent)).toEqual(['Choose a member…', 'Example Owner', 'Dara']);
        expect(screen.getByRole('button', { name: 'Insert mention' })).toBeDisabled();
    });

    it('refuses a blank or too long comment without calling the server', async () => {
        let posts = 0;
        server.use(
            http.post(commentsUrl, () => {
                posts += 1;
                return HttpResponse.json(commentFixture(), { status: 201 });
            }),
        );
        const user = await ready();
        await send(user);
        expect(screen.getByRole('alert')).toHaveTextContent('Write a comment before sending.');

        fireEvent.change(box(), { target: { value: '   \n  ' } });
        await send(user);
        expect(screen.getByRole('alert')).toHaveTextContent('Write a comment before sending.');

        fireEvent.change(box(), { target: { value: '😀'.repeat(2001) } });
        await send(user);
        expect(screen.getByRole('alert')).toHaveTextContent('Use 2000 characters or fewer');
        expect(posts).toBe(0);
    });

    it('refuses a mention that does not fit in the comment', async () => {
        const user = await ready();
        fireEvent.change(box(), { target: { value: 'x'.repeat(1996) } });
        await user.selectOptions(screen.getByLabelText('Mention a member'), 'Dara');
        await user.click(screen.getByRole('button', { name: 'Insert mention' }));
        expect(screen.getByRole('alert')).toHaveTextContent('does not fit');
        expect(box()).toHaveValue('x'.repeat(1996));
    });
});

describe('when the backend refuses', () => {
    it('shows the reason for a removed mention target, re-reads the members, and blocks sending until it is updated', async () => {
        let posts = 0;
        server.use(
            http.post(commentsUrl, () => {
                posts += 1;
                memberRows = [OWNER_ROW]; // Dara was removed meanwhile
                return HttpResponse.json(
                    errorBodyFixture('VALIDATION_FAILED', [{ field: 'mentions[0].user_id', reason: 'That person is not a member.' }]),
                    { status: 422 },
                );
            }),
        );
        const user = await ready();
        fireEvent.change(box(), { target: { value: 'Hi ' } });
        await user.selectOptions(screen.getByLabelText('Mention a member'), 'Dara');
        await user.click(screen.getByRole('button', { name: 'Insert mention' }));
        const before = memberReads;
        await send(user);

        expect(await screen.findByText('That person is not a member. Update the mentions and try again.')).toBeInTheDocument();
        await waitFor(() => expect(memberReads).toBeGreaterThan(before));
        await waitFor(() => expect(within(screen.getByLabelText('Mention a member')).getAllByRole('option')).toHaveLength(2));

        await send(user);
        expect(await screen.findByText(/Dara is no longer a member/)).toBeInTheDocument();
        expect(posts).toBe(1);
        expect(box()).toHaveValue('Hi @Dara '); // the draft is kept
    });

    it('shows a body problem reported by the server', async () => {
        server.use(
            http.post(commentsUrl, () =>
                HttpResponse.json(errorBodyFixture('VALIDATION_FAILED', [{ field: 'body', reason: 'Body is not allowed.' }]), {
                    status: 422,
                }),
            ),
        );
        const user = await ready();
        await user.type(box(), 'Hello');
        await send(user);
        expect(await screen.findByText('Body is not allowed.')).toBeInTheDocument();
        expect(box()).toHaveValue('Hello');
    });

    it('retries after an unclear failure with the same key and shows the comment once', async () => {
        const keys: (string | null)[] = [];
        let added = false;
        server.use(
            http.get(commentsUrl, () =>
                HttpResponse.json({ items: added ? [commentFixture({ body: 'Once', mentions: [] })] : [], next_cursor: null }),
            ),
            http.post(commentsUrl, ({ request }) => {
                keys.push(request.headers.get('idempotency-key'));
                if (keys.length === 1) return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), { status: 503 });
                added = true;
                return HttpResponse.json(commentFixture({ body: 'Once', mentions: [] }), { status: 201 });
            }),
        );
        const user = await ready();
        await user.type(box(), 'Once');
        await send(user);
        expect(await screen.findByText(/will not post a duplicate/)).toBeInTheDocument();
        expect(box()).toHaveValue('Once');

        await send(user);
        expect(await screen.findByText('Comment added.')).toBeInTheDocument();
        expect(keys[0]).toBe(keys[1]);
        expect(within(await list()).getAllByRole('listitem')).toHaveLength(1);
    });

    it('shows the task as unavailable when it was deleted or access was revoked', async () => {
        let gone = false;
        server.use(
            http.get(taskUrl, () =>
                gone
                    ? HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })
                    : HttpResponse.json(taskFixture(), { headers: { ETag: '"1"' } }),
            ),
            http.post(commentsUrl, () => {
                gone = true;
                return HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 });
            }),
        );
        const user = await ready();
        await user.type(box(), 'Hello');
        await send(user);
        expect(await screen.findByRole('heading', { name: 'Task not available' })).toBeInTheDocument();
    });
});
