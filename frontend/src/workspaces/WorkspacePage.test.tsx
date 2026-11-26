import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import { errorBodyFixture, WORKSPACE_ID, workspaceFixture } from '../test/fixtures';
import { API, meHandler, renderApp } from '../test/renderApp';
import { server } from '../test/server';

const wsUrl = `${API}/workspaces/${WORKSPACE_ID}`;
const open = () => renderApp({ signedIn: true, path: `/workspaces/${WORKSPACE_ID}` });
const getHandler = (overrides: Record<string, unknown> = {}, etag = '"2"') =>
    http.get(wsUrl, () => HttpResponse.json(workspaceFixture(overrides), { headers: { ETag: etag } }));

// Every workspace page also loads its member list.
beforeEach(() => {
    server.use(
        http.get(`${wsUrl}/members`, () => HttpResponse.json({ items: [] })),
        http.get(`${wsUrl}/invitations`, () => HttpResponse.json({ items: [], next_cursor: null })),
    );
});

describe('WorkspacePage view', () => {
    it('shows the workspace, the role, the size and the owner controls', async () => {
        server.use(getHandler());
        open();
        expect(screen.getByText('Loading workspace…')).toBeInTheDocument();
        expect(await screen.findByRole('heading', { name: 'DevOps Team' })).toBeInTheDocument();
        expect(screen.getByText(/Your role/)).toHaveTextContent('Owner · 2 of 20 members');
        expect(screen.getByRole('button', { name: 'Rename workspace' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Delete workspace' })).toBeInTheDocument();
    });

    it('shows no owner controls to a member', async () => {
        server.use(getHandler({ my_role: 'member' }));
        open();
        expect(await screen.findByRole('heading', { name: 'DevOps Team' })).toBeInTheDocument();
        expect(screen.getByText(/Your role/)).toHaveTextContent('Member');
        expect(screen.getByText('Only the workspace owner can rename or delete it.')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Rename workspace' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Delete workspace' })).not.toBeInTheDocument();
    });

    it('shows an error with a retry', async () => {
        let calls = 0;
        server.use(
            http.get(wsUrl, () => {
                calls += 1;
                if (calls === 1) {
                    return HttpResponse.json(errorBodyFixture('DEPENDENCY_UNAVAILABLE'), {
                        status: 503,
                        headers: { 'Retry-After': '2' },
                    });
                }
                return HttpResponse.json(workspaceFixture(), { headers: { ETag: '"2"' } });
            }),
        );
        const user = userEvent.setup();
        open();
        expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByRole('heading', { name: 'DevOps Team' })).toBeInTheDocument();
    });

    it('shows a not-available page when the workspace is hidden or deleted', async () => {
        server.use(http.get(wsUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })));
        open();
        expect(await screen.findByRole('heading', { name: 'Workspace not available' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Back to workspaces' })).toHaveAttribute('href', '/workspaces');
        expect(screen.queryByRole('button', { name: 'Delete workspace' })).not.toBeInTheDocument();
    });
});

describe('WorkspacePage rename', () => {
    it('renames with If-Match, then uses the new version for the next change', async () => {
        const seen: { ifMatch: string | null; body: unknown }[] = [];
        server.use(
            getHandler(),
            http.patch(wsUrl, async ({ request }) => {
                seen.push({ ifMatch: request.headers.get('if-match'), body: await request.json() });
                const version = seen.length + 2;
                return HttpResponse.json(workspaceFixture({ name: seen.length === 1 ? 'Renamed' : 'Renamed again', version }), {
                    headers: { ETag: `"${version}"` },
                });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'DevOps Team' });

        const input = screen.getByLabelText('Workspace name');
        await user.clear(input);
        await user.type(input, '  Renamed  ');
        await user.click(screen.getByRole('button', { name: 'Rename workspace' }));
        expect(await screen.findByRole('heading', { name: 'Renamed' })).toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveTextContent('Workspace renamed.');

        await user.clear(input);
        await user.type(input, 'Renamed again');
        await user.click(screen.getByRole('button', { name: 'Rename workspace' }));
        expect(await screen.findByRole('heading', { name: 'Renamed again' })).toBeInTheDocument();

        expect(seen).toEqual([
            { ifMatch: '"2"', body: { name: 'Renamed' } },
            { ifMatch: '"3"', body: { name: 'Renamed again' } },
        ]);
    });

    it('rejects an empty or unchanged name without calling the server', async () => {
        let patches = 0;
        server.use(
            getHandler(),
            http.patch(wsUrl, () => {
                patches += 1;
                return HttpResponse.json(workspaceFixture());
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'DevOps Team' });

        const input = screen.getByLabelText('Workspace name');
        await user.clear(input);
        await user.click(screen.getByRole('button', { name: 'Rename workspace' }));
        expect(screen.getByRole('alert')).toHaveTextContent('Enter a workspace name.');

        await user.type(input, 'DevOps Team');
        await user.click(screen.getByRole('button', { name: 'Rename workspace' }));
        expect(screen.getByRole('alert')).toHaveTextContent('Enter a different name');
        expect(patches).toBe(0);
    });

    it('shows the server reason for an invalid name', async () => {
        server.use(
            getHandler(),
            http.patch(wsUrl, () =>
                HttpResponse.json(errorBodyFixture('VALIDATION_FAILED', [{ field: 'name', reason: 'Name is not allowed.' }]), {
                    status: 422,
                }),
            ),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'DevOps Team' });
        const input = screen.getByLabelText('Workspace name');
        await user.clear(input);
        await user.type(input, 'Bad');
        await user.click(screen.getByRole('button', { name: 'Rename workspace' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Name is not allowed.');
    });

    it('reloads the latest version after a stale-version conflict and keeps what the user typed', async () => {
        let current = { name: 'DevOps Team', version: 2 };
        const ifMatches: (string | null)[] = [];
        server.use(
            http.get(wsUrl, () =>
                HttpResponse.json(workspaceFixture(current), { headers: { ETag: `"${current.version}"` } }),
            ),
            http.patch(wsUrl, ({ request }) => {
                ifMatches.push(request.headers.get('if-match'));
                if (ifMatches.length === 1) {
                    current = { name: 'Changed by teammate', version: 5 };
                    return HttpResponse.json(errorBodyFixture('VERSION_CONFLICT'), { status: 412 });
                }
                return HttpResponse.json(workspaceFixture({ name: 'My rename', version: 6 }), {
                    headers: { ETag: '"6"' },
                });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'DevOps Team' });

        const input = screen.getByLabelText('Workspace name');
        await user.clear(input);
        await user.type(input, 'My rename');
        await user.click(screen.getByRole('button', { name: 'Rename workspace' }));

        expect(await screen.findByRole('heading', { name: 'Changed by teammate' })).toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent('changed by someone else');
        expect(screen.getByRole('alert')).toHaveTextContent('latest version');
        expect(input).toHaveValue('My rename');

        await user.click(screen.getByRole('button', { name: 'Rename workspace' }));
        expect(await screen.findByRole('heading', { name: 'My rename' })).toBeInTheDocument();
        expect(ifMatches).toEqual(['"2"', '"5"']);
    });

    it('shows the not-available page if the workspace disappears during a rename', async () => {
        server.use(
            getHandler(),
            http.patch(wsUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'DevOps Team' });
        const input = screen.getByLabelText('Workspace name');
        await user.clear(input);
        await user.type(input, 'Anything');
        await user.click(screen.getByRole('button', { name: 'Rename workspace' }));
        expect(await screen.findByRole('heading', { name: 'Workspace not available' })).toBeInTheDocument();
    });
});

describe('WorkspacePage delete', () => {
    it('asks for confirmation, can be cancelled, and returns focus to the trigger', async () => {
        let deletes = 0;
        server.use(
            getHandler(),
            http.delete(wsUrl, () => {
                deletes += 1;
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'DevOps Team' });

        await user.click(screen.getByRole('button', { name: 'Delete workspace' }));
        expect(screen.getByRole('heading', { name: /Delete .*DevOps Team.*\?/ })).toBeInTheDocument();
        expect(screen.getByText(/This cannot be undone/)).toBeInTheDocument();
        expect(screen.getByText(/frees one of your 10 owned-workspace slots/)).toBeInTheDocument();
        await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus());

        await user.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByRole('button', { name: 'Yes, delete workspace' })).not.toBeInTheDocument();
        await waitFor(() => expect(screen.getByRole('button', { name: 'Delete workspace' })).toHaveFocus());
        expect(deletes).toBe(0);
    });

    it('deletes with If-Match after confirmation and returns to the list with a notice', async () => {
        let ifMatch: string | null = null;
        server.use(
            getHandler(),
            meHandler(0),
            http.get(`${API}/workspaces`, () => HttpResponse.json({ items: [], next_cursor: null })),
            http.delete(wsUrl, ({ request }) => {
                ifMatch = request.headers.get('if-match');
                return new HttpResponse(null, { status: 204 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'DevOps Team' });

        await user.click(screen.getByRole('button', { name: 'Delete workspace' }));
        await user.click(screen.getByRole('button', { name: 'Yes, delete workspace' }));

        expect(await screen.findByText(/DevOps Team.* deleted/)).toBeInTheDocument();
        expect(screen.getByRole('heading', { name: 'Workspaces' })).toBeInTheDocument();
        expect(ifMatch).toBe('"2"');
    });

    it('keeps the confirmation open with the latest version after a stale-version conflict', async () => {
        let current = { name: 'DevOps Team', version: 2 };
        server.use(
            http.get(wsUrl, () =>
                HttpResponse.json(workspaceFixture(current), { headers: { ETag: `"${current.version}"` } }),
            ),
            http.delete(wsUrl, () => {
                current = { name: 'Renamed meanwhile', version: 4 };
                return HttpResponse.json(errorBodyFixture('VERSION_CONFLICT'), { status: 412 });
            }),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'DevOps Team' });
        await user.click(screen.getByRole('button', { name: 'Delete workspace' }));
        await user.click(screen.getByRole('button', { name: 'Yes, delete workspace' }));

        expect(await screen.findByRole('alert')).toHaveTextContent('Confirm again');
        expect(await screen.findByRole('heading', { name: 'Renamed meanwhile' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Yes, delete workspace' })).toBeEnabled();
    });

    it('shows the not-available page when the workspace is already gone', async () => {
        server.use(
            getHandler(),
            http.delete(wsUrl, () => HttpResponse.json(errorBodyFixture('NOT_FOUND'), { status: 404 })),
        );
        const user = userEvent.setup();
        open();
        await screen.findByRole('heading', { name: 'DevOps Team' });
        await user.click(screen.getByRole('button', { name: 'Delete workspace' }));
        await user.click(screen.getByRole('button', { name: 'Yes, delete workspace' }));
        expect(await screen.findByRole('heading', { name: 'Workspace not available' })).toBeInTheDocument();
    });
});