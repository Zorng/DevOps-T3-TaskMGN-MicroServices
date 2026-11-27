import { describe, expect, it } from 'vitest';
import { memberFixture, MEMBER_ID } from '../test/fixtures';
import { parseMember } from '../workspaces/workspacesApi';
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

const DARA = { userId: MEMBER_ID, name: 'Dara' };
const members = [parseMember(memberFixture())!];
const draft = (start: number, name = 'Dara', userId = MEMBER_ID): MentionDraft => ({
    userId,
    name,
    start,
    length: cpLength(`@${name}`),
});
const textAt = (body: string, mention: MentionDraft) =>
    Array.from(body)
        .slice(mention.start, mention.start + mention.length)
        .join('');

describe('code point helpers', () => {
    it('count an emoji as one character', () => {
        expect(cpLength('😀')).toBe(1);
        expect('😀'.length).toBe(2);
        expect(toCodePointOffset('😀 hi', 3)).toBe(2);
        expect(toUtf16Index('😀 hi', 2)).toBe(3);
    });
});

describe('insertMention', () => {
    it('records the range in code points when an emoji comes first', () => {
        const result = insertMention('😀 hi ', [], 5, 5, DARA);
        if (!result.ok) throw new Error('expected success');
        expect(result.body).toBe('😀 hi @Dara ');
        expect(result.mentions).toEqual([{ userId: MEMBER_ID, name: 'Dara', start: 5, length: 5 }]);
        expect(textAt(result.body, result.mentions[0]!)).toBe('@Dara');
        // The same position counted in UTF-16 units would be one higher, which the server would reject.
        expect(result.body.indexOf('@')).toBe(6);
        expect(result.caret).toBe(11);
    });

    it('replaces the selected text', () => {
        const result = insertMention('Hello world', [], 6, 11, DARA);
        if (!result.ok) throw new Error('expected success');
        expect(result.body).toBe('Hello @Dara ');
        expect(result.mentions[0]?.start).toBe(6);
    });

    it('shifts a later mention and keeps mentions in order', () => {
        const result = insertMention('@Dara x', [draft(0)], 0, 0, { userId: 'u2', name: 'Lisa' });
        if (!result.ok) throw new Error('expected success');
        expect(result.body).toBe('@Lisa @Dara x');
        expect(result.mentions.map((m) => [m.name, m.start])).toEqual([
            ['Lisa', 0],
            ['Dara', 6],
        ]);
    });

    it('drops a mention that the insertion lands inside of', () => {
        const result = insertMention('@Dara', [draft(0)], 2, 2, { userId: 'u2', name: 'Lisa' });
        if (!result.ok) throw new Error('expected success');
        expect(result.mentions.map((m) => m.name)).toEqual(['Lisa']);
    });

    it('refuses a mention that would exceed the comment length', () => {
        expect(insertMention('x'.repeat(1995), [], 1995, 1995, DARA)).toMatchObject({ ok: false });
        expect(insertMention('x'.repeat(1994), [], 1994, 1994, DARA)).toMatchObject({ ok: true });
    });

    it('refuses a 51st range and a 21st different person', () => {
        const fifty = Array.from({ length: 50 }, (_, i) => draft(i * 6));
        expect(insertMention('', fifty, 0, 0, DARA)).toMatchObject({ ok: false, message: expect.stringContaining('50') });
        const twenty = Array.from({ length: 20 }, (_, i) => draft(i * 6, 'Dara', `user-${i}`));
        expect(insertMention('', twenty, 0, 0, DARA)).toMatchObject({ ok: false, message: expect.stringContaining('20') });
    });
});

describe('reconcileMentions (ranges stay aligned after edits)', () => {
    const body = 'Hi @Dara ok';
    const mentions = [draft(3)];

    it('shifts a mention when text is typed before it', () => {
        const next = 'Oh Hi @Dara ok';
        const result = reconcileMentions(body, next, mentions);
        expect(result).toHaveLength(1);
        expect(textAt(next, result[0]!)).toBe('@Dara');
    });

    it('leaves a mention alone when text is typed after it', () => {
        expect(reconcileMentions(body, 'Hi @Dara ok!', mentions)).toEqual(mentions);
    });

    it('drops a mention that was edited inside, because it is ordinary text now', () => {
        expect(reconcileMentions(body, 'Hi @Dxra ok', mentions)).toEqual([]);
    });

    it('drops a mention when part of it is deleted or the whole text is replaced', () => {
        expect(reconcileMentions(body, 'Hi @Dar ok', mentions)).toEqual([]);
        expect(reconcileMentions(body, 'something else', mentions)).toEqual([]);
    });

    it('follows deletions before it, counting an emoji as one character', () => {
        const emojiBody = '😀😀 @Dara';
        const next = '😀 @Dara';
        const result = reconcileMentions(emojiBody, next, [draft(3)]);
        expect(result.map((m) => m.start)).toEqual([2]);
        expect(textAt(next, result[0]!)).toBe('@Dara');
    });

    it('handles a repeated character next to the mention', () => {
        const result = reconcileMentions('a@Dara', 'aa@Dara', [draft(1)]);
        expect(result.map((m) => m.start)).toEqual([2]);
    });

    it('does nothing for ordinary typed @text, which was never a mention', () => {
        expect(reconcileMentions('', 'hello @Dara', [])).toEqual([]);
    });
});

describe('validateComment', () => {
    it('refuses blank and whitespace-only comments', () => {
        expect(validateComment('', [], members)).toEqual({ ok: false, message: 'Write a comment before sending.' });
        expect(validateComment('  \n ', [], members)).toMatchObject({ ok: false });
    });

    it('sends the body exactly as typed, so offsets stay valid', () => {
        const result = validateComment('  Hi @Dara  ', [draft(5)], members);
        expect(result).toEqual({
            ok: true,
            request: { body: '  Hi @Dara  ', mentions: [{ user_id: MEMBER_ID, start: 5, length: 5 }] },
        });
    });

    it('sends ranges counted in code points after an emoji', () => {
        const result = validateComment('😀 Hi @Dara', [draft(5)], members);
        expect(result).toMatchObject({ ok: true, request: { mentions: [{ start: 5, length: 5 }] } });
    });

    it('counts the length in code points', () => {
        expect(validateComment('😀'.repeat(COMMENT_BODY_MAX), [], members).ok).toBe(true);
        expect(validateComment('😀'.repeat(COMMENT_BODY_MAX + 1), [], members)).toMatchObject({ ok: false });
    });

    it('refuses overlapping ranges', () => {
        // "@a@b" is the mention of "a@b" (0 to 4) and, inside it, the mention of "b" (2 to 4).
        const result = validateComment('@a@b', [draft(0, 'a@b'), draft(2, 'b')], members);
        expect(result).toEqual({ ok: false, message: 'Mentions cannot overlap.' });
    });

    it('refuses a range that does not fit the text or no longer reads @name', () => {
        expect(validateComment('Hi', [draft(3)], members)).toMatchObject({ ok: false });
        expect(validateComment('Hi @Dxra', [draft(3)], members)).toMatchObject({ ok: false });
    });

    it('refuses more than 50 ranges and more than 20 different people', () => {
        const fifty1 = Array.from({ length: 51 }, (_, i) => draft(i * 6));
        expect(validateComment('x'.repeat(400), fifty1, members)).toMatchObject({ ok: false, message: expect.stringContaining('50') });
        const twenty1 = Array.from({ length: 21 }, (_, i) => draft(i * 6, 'Dara', `user-${i}`));
        expect(validateComment('x'.repeat(400), twenty1, members)).toMatchObject({ ok: false, message: expect.stringContaining('20') });
    });

    it('refuses a mention of someone who is no longer a member', () => {
        const result = validateComment('@Gone', [draft(0, 'Gone', 'gone-id')], members);
        expect(result).toMatchObject({ ok: false, message: expect.stringContaining('Gone is no longer a member') });
    });
});

describe('segmentsOf', () => {
    it('splits a stored comment by code point ranges', () => {
        expect(segmentsOf('😀 Hi @Dara!', [{ start: 5, length: 5 }])).toEqual([
            { text: '😀 Hi ', mention: false },
            { text: '@Dara', mention: true },
            { text: '!', mention: false },
        ]);
    });

    it('shows everything as plain text when there are no ranges', () => {
        expect(segmentsOf('ping @Dara', [])).toEqual([{ text: 'ping @Dara', mention: false }]);
    });

    it('ignores ranges that do not fit, overlap, or do not start with @', () => {
        expect(segmentsOf('Hello', [{ start: 3, length: 5 }])).toEqual([{ text: 'Hello', mention: false }]);
        expect(segmentsOf('Hello', [{ start: 0, length: 2 }])).toEqual([{ text: 'Hello', mention: false }]);
        expect(
            segmentsOf('@a@b', [
                { start: 0, length: 4 },
                { start: 2, length: 2 },
            ]),
        ).toEqual([{ text: '@a@b', mention: false }]);
    });
});
