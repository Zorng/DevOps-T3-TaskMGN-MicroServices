import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { ApiError, isForbidden, isNotFound } from '../api/errors';
import { describeError, fieldReason } from '../api/messages';
import { usePageLoader } from '../api/usePageLoader';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import type { Member } from '../workspaces/workspacesApi';
import { commentsPath, createCommentAttempt, type Comment } from './commentsApi';
import {
    COMMENT_BODY_MAX,
    cpLength,
    insertMention,
    reconcileMentions,
    segmentsOf,
    toCodePointOffset,
    toUtf16Index,
    validateComment,
    type MentionDraft,
} from './mentions';

interface CommentsSectionProps {
    workspaceId: string;
    taskId: string;
    /** Current workspace members: the only people who can be mentioned. */
    members: Member[];
    /** Re-read the task page (members, access) after a refusal that may mean something changed. */
    onStale: () => Promise<void>;
}

interface PendingCreate {
    key: string;
    create: ReturnType<typeof createCommentAttempt>;
}

/** The server's reason for a rejected mention, if the 422 names one of the mention fields. */
function mentionReason(error: unknown): string | undefined {
    if (!(error instanceof ApiError) || error.code !== 'VALIDATION_FAILED') return undefined;
    return error.details.find((detail) => detail.field.startsWith('mentions'))?.reason;
}

function CommentItem({ comment }: { comment: Comment }) {
    return (
        <li>
            <p>
                <strong>{comment.author.display_name}</strong>
                {' · '}
                <time dateTime={comment.created_at}>{comment.created_at}</time>
            </p>
            {/* Plain text only: React escapes every piece, so markup typed by a user is shown, never run. */}
            <p style={{ whiteSpace: 'pre-wrap' }}>
                {segmentsOf(comment.body, comment.mentions).map((segment, index) =>
                    segment.mention ? <strong key={index}>{segment.text}</strong> : <span key={index}>{segment.text}</span>,
                )}
            </p>
        </li>
    );
}

export function CommentsSection({ workspaceId, taskId, members, onStale }: CommentsSectionProps) {
    const { client } = useAuth();
    const { items, status, error, hasMore, loadMore, reload } = usePageLoader<Comment>(client, {
        path: commentsPath(workspaceId, taskId),
    });

    const [body, setBody] = useState('');
    const [mentions, setMentions] = useState<MentionDraft[]>([]);
    const [picked, setPicked] = useState('');
    const [formError, setFormError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [sending, setSending] = useState(false);
    const pending = useRef<PendingCreate | null>(null);
    const textarea = useRef<HTMLTextAreaElement>(null);
    const caret = useRef<number | null>(null);

    // After a mention is inserted, put the caret right after it so the user keeps typing.
    useEffect(() => {
        if (caret.current === null || !textarea.current) return;
        const index = toUtf16Index(body, caret.current);
        textarea.current.focus();
        textarea.current.setSelectionRange(index, index);
        caret.current = null;
    }, [body]);

    function handleChange(event: ChangeEvent<HTMLTextAreaElement>) {
        const next = event.target.value;
        setMentions((current) => reconcileMentions(body, next, current));
        setBody(next);
    }

    function handleInsert() {
        const member = members.find((item) => item.user.id === picked);
        if (!member) return;
        const field = textarea.current;
        const start = toCodePointOffset(body, field?.selectionStart ?? body.length);
        const end = toCodePointOffset(body, field?.selectionEnd ?? body.length);
        const result = insertMention(body, mentions, start, end, {
            userId: member.user.id,
            name: member.user.display_name,
        });
        if (!result.ok) {
            setFormError(result.message);
            return;
        }
        setFormError(null);
        caret.current = result.caret;
        setBody(result.body);
        setMentions(result.mentions);
        setPicked('');
    }

    async function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (sending) return;
        const check = validateComment(body, mentions, members);
        if (!check.ok) {
            setFormError(check.message);
            return;
        }
        setFormError(null);
        setNotice(null);

        // The same request keeps the same key, so pressing the button again after an unclear failure cannot duplicate it.
        const key = JSON.stringify(check.request);
        let current = pending.current;
        if (!current || current.key !== key || current.create.attempt.state === 'rejected') {
            current = { key, create: createCommentAttempt(client, workspaceId, taskId, check.request) };
            pending.current = current;
        }

        setSending(true);
        try {
            await current.create.send();
            pending.current = null;
            setBody('');
            setMentions([]);
            setNotice('Comment added.');
            reload(); // read the list again from the server, so a retry cannot show a comment twice
        } catch (caught) {
            const specific = mentionReason(caught);
            const validation = caught instanceof ApiError && caught.code === 'VALIDATION_FAILED';
            const mentionProblem = specific !== undefined || (validation && check.request.mentions.length > 0 && !fieldReason(caught, 'body'));
            let text: string;
            if (specific) text = `${specific} Update the mentions and try again.`;
            else if (mentionProblem) text = 'The comment was rejected. A mentioned person may no longer be a member. Update the mentions and try again.';
            else text = fieldReason(caught, 'body') ?? describeError(caught, 'The comment could not be added.');
            if (current.create.attempt.state === 'retryable') {
                text = `${text} Press “Add comment” again to retry. It will not post a duplicate.`;
            }
            setFormError(text);
            // The server decides: re-read the members (a removed person) and the task (revoked access, deleted task).
            if (mentionProblem || isNotFound(caught) || isForbidden(caught)) await onStale();
        } finally {
            setSending(false);
        }
    }

    const length = cpLength(body);

    return (
        <section aria-labelledby="comments-title">
            <h2 id="comments-title">Comments</h2>

            {status === 'loading' && items.length === 0 && <LoadingState label="Loading comments…" />}
            {status === 'error' && items.length === 0 && error && (
                <ErrorState message={describeError(error)} onRetry={loadMore} />
            )}
            {status === 'ready' && items.length === 0 && (
                <EmptyState title="No comments yet" message="Be the first to comment on this task." />
            )}
            {items.length > 0 && (
                <ul aria-label="Comments">
                    {items.map((comment) => (
                        <CommentItem key={comment.id} comment={comment} />
                    ))}
                </ul>
            )}
            {items.length > 0 && status === 'loading' && <LoadingState label="Loading more comments…" />}
            {items.length > 0 && status === 'error' && error && (
                <ErrorState message={describeError(error)} onRetry={loadMore} retryLabel="Try loading more again" />
            )}
            {status === 'ready' && hasMore && (
                <button type="button" onClick={loadMore}>
                    Load more comments
                </button>
            )}
            {notice && <p role="status">{notice}</p>}

            <form onSubmit={handleSubmit} noValidate aria-label="Add a comment">
                <label htmlFor="comment-body">Comment</label>
                <textarea
                    id="comment-body"
                    ref={textarea}
                    value={body}
                    onChange={handleChange}
                    aria-invalid={formError !== null}
                    aria-describedby={formError ? 'comment-error' : undefined}
                />
                <p>{`${length} of ${COMMENT_BODY_MAX} characters. Plain text only.`}</p>

                <label htmlFor="comment-mention">Mention a member</label>
                <select id="comment-mention" value={picked} onChange={(event) => setPicked(event.target.value)}>
                    <option value="">Choose a member…</option>
                    {members.map((member) => (
                        <option key={member.user.id} value={member.user.id}>
                            {member.user.display_name}
                        </option>
                    ))}
                </select>
                <button type="button" disabled={picked === ''} onClick={handleInsert}>
                    Insert mention
                </button>
                <p>Typing @ and a name does not mention anyone. Pick a member from the list to mention them.</p>

                {mentions.length > 0 && (
                    <ul aria-label="Mentions in this comment">
                        {mentions.map((mention, index) => (
                            <li key={`${mention.userId}-${mention.start}`}>
                                <span>{mention.name}</span>
                                <button
                                    type="button"
                                    aria-label={`Remove mention of ${mention.name}`}
                                    onClick={() => setMentions((current) => current.filter((_, i) => i !== index))}
                                >
                                    Remove
                                </button>
                            </li>
                        ))}
                    </ul>
                )}

                <button type="submit" disabled={sending}>
                    Add comment
                </button>
                {formError && (
                    <p id="comment-error" role="alert">
                        {formError}
                    </p>
                )}
            </form>
        </section>
    );
}
