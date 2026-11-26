import { ApiError } from './errors';

export const GENERIC_ERROR_MESSAGE = 'Something went wrong. Please try again.';

/** Turns an error into text for the user, chosen by code (never the server's own message). */
export function describeError(error: unknown, fallback: string = GENERIC_ERROR_MESSAGE): string {
    if (!(error instanceof ApiError)) return fallback;

    switch (error.code) {
        case 'NETWORK_ERROR':
            return 'The service could not be reached. Check your connection and try again.';
        case 'ORIGIN_NOT_ALLOWED':
            return 'This site address is not allowed to use the service yet. Ask the team to add it to the allowed origins.';
        case 'AUTH_CHALLENGE_INVALID':
            return 'The sign-in attempt expired. Please try again.';
        case 'GOOGLE_TOKEN_INVALID':
            return 'Google sign-in could not be verified. Please try again.';
        case 'AUTH_REQUIRED':
        case 'SESSION_INVALID':
            return 'Your session has ended. Please sign in again.';
        case 'RATE_LIMITED':
            return error.retryAfterSeconds === undefined
            ? 'Too many attempts. Please wait a moment and try again.'
            : `Too many attempts. Please wait ${error.retryAfterSeconds} seconds and try again.`;
        case 'DEPENDENCY_UNAVAILABLE':
            return error.retryAfterSeconds === undefined
                ? 'The service is temporarily unavailable. Please try again shortly.'
                : `The service is temporarily unavailable. Please try again in about ${error.retryAfterSeconds} seconds.`;
        case 'WORKSPACE_LIMIT_REACHED':
            return 'You already own 10 workspaces, the maximum. Workspaces you have joined do not count toward this limit. Delete one of your own workspaces to create another.';
        case 'VALIDATION_FAILED':
            return 'The information you entered is not valid. Check it and try again.';
        case 'IDEMPOTENCY_IN_PROGRESS':
            return 'Your request is still being processed. Please wait a moment and try again.';
        case 'VERSION_CONFLICT':
            return 'This was changed by someone else since you loaded it. Review the latest version and try again.';
        case 'FORBIDDEN':
            return 'You do not have permission to do that in this workspace.';
        case 'NOT_FOUND':
            return 'This workspace is not available. It may have been deleted, or you may no longer have access.';
        case 'OWNER_CANNOT_LEAVE':
            return 'The owner cannot leave a workspace. Delete the workspace instead.';
        case 'USER_NOT_FOUND':
            return 'No registered user has that email address. They need to sign in to this app once before they can be invited.';
        case 'EMAIL_AMBIGUOUS':
            return 'That email address matches more than one account, so it cannot be used to invite someone.';
        case 'ALREADY_MEMBER':
            return 'That person is already a member of this workspace.';
        case 'INVITATION_ALREADY_PENDING':
            return 'That person already has a pending invitation to this workspace.';
        case 'MEMBER_LIMIT_REACHED':
            return 'This workspace already has the maximum of 20 members. Invitations cannot be accepted until a member leaves or is removed.';
        case 'INVITATION_NOT_PENDING':
            return 'This invitation has already been accepted, declined or cancelled, so it cannot be changed.';
        case 'INTERNAL_ERROR':
            return 'The service ran into a problem. Please try again.';
        default:
            return fallback;
    }
}

/** The server's reason for one field of a VALIDATION_FAILED error, if it gave one. */
export function fieldReason(error: unknown, field: string): string | undefined {
    if (!(error instanceof ApiError) || error.code !== 'VALIDATION_FAILED') return undefined;
    return error.details.find((detail) => detail.field === field)?.reason;
}