import type { Member } from '../workspaces/workspacesApi';

export const COMMENT_BODY_MAX = 2000;
export const MENTION_RANGES_MAX = 50;
export const MENTION_USERS_MAX = 20;

/**
 * A mention the user picked. `start` and `length` count Unicode code points in the exact body,
 * and the text at that range is always "@" + name. The user id, not the text, decides who is notified.
 */
export interface MentionDraft {
    userId: string;
    name: string;
    start: number;
    length: number;
}

const points = (text: string): string[] => Array.from(text);

/** Length in Unicode code points (an emoji counts as one, not two UTF-16 units). */
export const cpLength = (text: string): number => points(text).length;

/** Code point offset of a UTF-16 index, such as a textarea's selectionStart. */
export const toCodePointOffset = (text: string, utf16Index: number): number => points(text.slice(0, utf16Index)).length;

/** UTF-16 index of a code point offset, for setting a textarea's caret. */
export const toUtf16Index = (text: string, offset: number): number => points(text).slice(0, offset).join('').length;

const mentionText = (name: string) => `@${name}`;

/**
 * Moves mentions after the text between editStart and editEnd (code points) was replaced by
 * `insertedLength` new code points. Mentions before the edit stay, mentions after it shift,
 * and a mention the edit touches is dropped: its text is now ordinary text.
 */
export function shiftMentions(
    mentions: MentionDraft[],
    editStart: number,
    editEnd: number,
    insertedLength: number,
): MentionDraft[] {
    const delta = insertedLength - (editEnd - editStart);
    const kept: MentionDraft[] = [];
    for (const mention of mentions) {
        const end = mention.start + mention.length;
        if (end <= editStart) kept.push(mention);
        else if (mention.start >= editEnd) kept.push({ ...mention, start: mention.start + delta });
    }
    return kept;
}

/**
 * Keeps mentions aligned after any edit of a plain text box. It finds the changed region by comparing
 * the old and new text, shifts or drops mentions accordingly, and finally keeps only mentions whose
 * text still reads exactly "@name".
 */
export function reconcileMentions(oldBody: string, newBody: string, mentions: MentionDraft[]): MentionDraft[] {
    if (mentions.length === 0) return mentions;
    const before = points(oldBody);
    const after = points(newBody);
    let prefix = 0;
    while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
    let suffix = 0;
    while (
        suffix < before.length - prefix &&
        suffix < after.length - prefix &&
        before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
    ) {
        suffix += 1;
    }
    const shifted = shiftMentions(mentions, prefix, before.length - suffix, after.length - suffix - prefix);
    return shifted.filter(
        (mention) => after.slice(mention.start, mention.start + mention.length).join('') === mentionText(mention.name),
    );
}

export type InsertResult =
    | { ok: true; body: string; mentions: MentionDraft[]; caret: number }
    | { ok: false; message: string };

/**
 * Inserts "@Name " at the selection (code point offsets), replacing any selected text,
 * and records the range of "@Name" for the picked member. Refuses when a bound would be exceeded.
 */
export function insertMention(
    body: string,
    mentions: MentionDraft[],
    selectionStart: number,
    selectionEnd: number,
    member: { userId: string; name: string },
): InsertResult {
    const chars = points(body);
    const inserted = `${mentionText(member.name)} `;
    const newBody = chars.slice(0, selectionStart).join('') + inserted + chars.slice(selectionEnd).join('');
    if (cpLength(newBody) > COMMENT_BODY_MAX) {
        return { ok: false, message: `The mention does not fit: a comment can have at most ${COMMENT_BODY_MAX} characters.` };
    }
    const shifted = shiftMentions(mentions, selectionStart, selectionEnd, cpLength(inserted));
    if (shifted.length >= MENTION_RANGES_MAX) {
        return { ok: false, message: `A comment can have at most ${MENTION_RANGES_MAX} mentions.` };
    }
    const users = new Set(shifted.map((mention) => mention.userId));
    if (!users.has(member.userId) && users.size >= MENTION_USERS_MAX) {
        return { ok: false, message: `A comment can mention at most ${MENTION_USERS_MAX} different people.` };
    }
    const added: MentionDraft = {
        userId: member.userId,
        name: member.name,
        start: selectionStart,
        length: cpLength(mentionText(member.name)),
    };
    return {
        ok: true,
        body: newBody,
        mentions: [...shifted, added].sort((a, b) => a.start - b.start),
        caret: selectionStart + cpLength(inserted),
    };
}

export interface CommentRequest {
    body: string;
    mentions: { user_id: string; start: number; length: number }[];
}

export type CommentCheck = { ok: true; request: CommentRequest } | { ok: false; message: string };

/**
 * Local checks that mirror the contract. The body is sent exactly as typed (never trimmed, which would
 * shift the offsets); only a body that is all whitespace is refused.
 */
export function validateComment(body: string, mentions: MentionDraft[], members: Member[]): CommentCheck {
    if (body.trim() === '') return { ok: false, message: 'Write a comment before sending.' };
    const length = cpLength(body);
    if (length > COMMENT_BODY_MAX) {
        return { ok: false, message: `Use ${COMMENT_BODY_MAX} characters or fewer. This comment has ${length}.` };
    }
    if (mentions.length > MENTION_RANGES_MAX) {
        return { ok: false, message: `A comment can have at most ${MENTION_RANGES_MAX} mentions.` };
    }
    if (new Set(mentions.map((mention) => mention.userId)).size > MENTION_USERS_MAX) {
        return { ok: false, message: `A comment can mention at most ${MENTION_USERS_MAX} different people.` };
    }

    const chars = points(body);
    const sorted = [...mentions].sort((a, b) => a.start - b.start);
    let previousEnd = 0;
    for (const mention of sorted) {
        const end = mention.start + mention.length;
        const fits = mention.start >= 0 && mention.length >= 2 && end <= chars.length;
        if (!fits || chars.slice(mention.start, end).join('') !== mentionText(mention.name)) {
            return { ok: false, message: 'A mention no longer matches the text. Remove it and add it again.' };
        }
        if (mention.start < previousEnd) return { ok: false, message: 'Mentions cannot overlap.' };
        previousEnd = end;
    }
    for (const mention of sorted) {
        if (!members.some((member) => member.user.id === mention.userId)) {
            return {
                ok: false,
                message: `${mention.name} is no longer a member. Remove that mention before sending.`,
            };
        }
    }
    return {
        ok: true,
        request: {
            body,
            mentions: sorted.map((mention) => ({ user_id: mention.userId, start: mention.start, length: mention.length })),
        },
    };
}

export interface Segment {
    text: string;
    mention: boolean;
}

/**
 * Splits a stored comment into plain and mentioned pieces by its code point ranges.
 * Ranges that do not fit, overlap, or do not start with "@" are ignored, so the whole body is shown as plain text.
 */
export function segmentsOf(body: string, ranges: { start: number; length: number }[]): Segment[] {
    const chars = points(body);
    const sorted = [...ranges].sort((a, b) => a.start - b.start);
    let previousEnd = 0;
    for (const range of sorted) {
        const end = range.start + range.length;
        const valid = range.start >= previousEnd && range.length >= 2 && end <= chars.length && chars[range.start] === '@';
        if (!valid) return [{ text: body, mention: false }];
        previousEnd = end;
    }
    const segments: Segment[] = [];
    let cursor = 0;
    for (const range of sorted) {
        if (range.start > cursor) segments.push({ text: chars.slice(cursor, range.start).join(''), mention: false });
        segments.push({ text: chars.slice(range.start, range.start + range.length).join(''), mention: true });
        cursor = range.start + range.length;
    }
    if (cursor < chars.length) segments.push({ text: chars.slice(cursor).join(''), mention: false });
    return segments.length > 0 ? segments : [{ text: body, mention: false }];
}
