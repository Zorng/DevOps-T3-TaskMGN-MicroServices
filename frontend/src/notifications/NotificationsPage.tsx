import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { isNotFound } from '../api/errors';
import { describeError } from '../api/messages';
import { usePageLoader } from '../api/usePageLoader';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import {
    getUnreadCount,
    markNotificationRead,
    notificationTarget,
    NOTIFICATIONS_PATH,
    TYPE_LABEL,
    TYPE_SENTENCE,
    type Notification,
} from './notificationsApi';

type Message = { kind: 'ok' | 'error'; text: string };

export function NotificationsPage() {
    const { client } = useAuth();
    const [unreadOnly, setUnreadOnly] = useState(false);
    // A new filter means a new loader: a cursor only works with the filter that issued it.
    const { items, status, error, hasMore, loadMore, reload } = usePageLoader<Notification>(client, {
        path: NOTIFICATIONS_PATH,
        query: { unread_only: unreadOnly ? 'true' : undefined },
    });

    const [count, setCount] = useState<number | null>(null);
    const [countFailed, setCountFailed] = useState(false);
    // Authoritative copies returned by "mark as read", shown over the loaded list until the next refresh.
    const [updated, setUpdated] = useState<Record<string, Notification>>({});
    const [marking, setMarking] = useState<Set<string>>(new Set());
    const inFlight = useRef<Set<string>>(new Set());
    const [message, setMessage] = useState<Message | null>(null);

    /** The unread count always comes from the server. It is never worked out by adding or subtracting locally. */
    const refreshCount = useCallback(async () => {
        try {
            setCount(await getUnreadCount(client));
            setCountFailed(false);
        } catch {
            setCountFailed(true); // keep showing the last known number, marked as possibly out of date
        }
    }, [client]);

    useEffect(() => {
        void refreshCount();
    }, [refreshCount]);

    function refresh() {
        setUpdated({});
        setMessage(null);
        reload();
        void refreshCount();
    }

    async function markRead(notification: Notification) {
        const current = updated[notification.id] ?? notification;
        // Already read, or a request is already running: send nothing, so nothing can be counted twice.
        if (current.read_at !== null || inFlight.current.has(notification.id)) return;
        inFlight.current.add(notification.id);
        setMarking(new Set(inFlight.current));
        setMessage(null);
        try {
            const result = await markNotificationRead(client, notification.id);
            setUpdated((previous) => ({ ...previous, [notification.id]: result }));
            await refreshCount();
        } catch (caught) {
            // Nothing changed as far as we know, so neither the item nor the count is touched.
            if (isNotFound(caught)) {
                setMessage({ kind: 'error', text: 'This notification is no longer available. The latest list is shown.' });
                reload();
                void refreshCount();
            } else {
                setMessage({ kind: 'error', text: describeError(caught, 'The notification could not be marked as read.') });
            }
        } finally {
            inFlight.current.delete(notification.id);
            setMarking(new Set(inFlight.current));
        }
    }

    return (
        <>
            <h1>Notifications</h1>
            <p>
                Invitations, task assignments and mentions addressed to you, newest first. This list does not update by
                itself: use Refresh to check for new ones. A notification never gives access; the page it opens checks
                your access again.
            </p>

            <p role="status">
                {count === null
                    ? countFailed
                        ? 'The unread count could not be loaded.'
                        : 'Loading unread count…'
                    : `Unread: ${count}${countFailed ? ' (could not be refreshed, may be out of date)' : ''}`}
            </p>

            <label htmlFor="unread-only">
                <input
                    id="unread-only"
                    type="checkbox"
                    checked={unreadOnly}
                    onChange={(event) => setUnreadOnly(event.target.checked)}
                />
                Show unread only
            </label>
            <button type="button" onClick={refresh}>
                Refresh
            </button>

            {message && <p role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>}

            {status === 'loading' && items.length === 0 && <LoadingState label="Loading notifications…" />}
            {status === 'error' && items.length === 0 && error && (
                <ErrorState message={describeError(error)} onRetry={loadMore} />
            )}
            {status === 'ready' && items.length === 0 && (
                <EmptyState
                    title={unreadOnly ? 'No unread notifications' : 'No notifications'}
                    message={
                        unreadOnly
                            ? 'You have read everything. Untick “Show unread only” to see earlier notifications.'
                            : 'Nothing has been sent to you yet.'
                    }
                />
            )}

            {items.length > 0 && (
                <ul aria-label="Notifications">
                    {items.map((loaded) => {
                        const notification = updated[loaded.id] ?? loaded;
                        const unread = notification.read_at === null;
                        const target = notificationTarget(notification);
                        return (
                            <li key={notification.id}>
                                <span>{TYPE_LABEL[notification.type]}</span>
                                {' · '}
                                <strong>{notification.actor.display_name}</strong>
                                {` ${TYPE_SENTENCE[notification.type]}`}
                                {' · '}
                                <time dateTime={notification.occurred_at}>{notification.occurred_at}</time>
                                {' · '}
                                <span>{unread ? 'Unread' : 'Read'}</span>{' '}
                                {target && <Link to={target.to}>{target.label}</Link>}
                                {unread && (
                                    <button
                                        type="button"
                                        disabled={marking.has(notification.id)}
                                        onClick={() => void markRead(notification)}
                                    >
                                        Mark as read
                                    </button>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}
            {items.length > 0 && status === 'loading' && <LoadingState label="Loading more notifications…" />}
            {items.length > 0 && status === 'error' && error && (
                <ErrorState message={describeError(error)} onRetry={loadMore} retryLabel="Try loading more again" />
            )}
            {status === 'ready' && hasMore && (
                <button type="button" onClick={loadMore}>
                    Load more notifications
                </button>
            )}
        </>
    );
}
