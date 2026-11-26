import type { RequestHandler } from 'express';
import { sendError } from './http.js';
import type { IntrospectSession } from './identity.js';

export function requireSession(introspect: IntrospectSession): RequestHandler {
  return async (request, response, next) => {
    const bearer = request.get('Authorization') ?? '';
    if (!/^Bearer +[A-Za-z0-9._~+/-]+=*$/i.test(bearer)) {
      sendError(response, 401, 'AUTH_REQUIRED', 'A bearer session is required.');
      return;
    }

    const requestId = String(response.get('X-Request-ID'));
    const result = await introspect(bearer, requestId);
    switch (result.kind) {
      case 'verified':
        response.locals.recipientId = result.recipientId;
        next();
        return;
      case 'invalid':
        sendError(response, 401, 'SESSION_INVALID', 'The session is invalid or expired.');
        return;
      case 'credential_rejected':
        console.error(JSON.stringify({
          service: 'notification',
          event: 'identity_service_credential_rejected',
          request_id: requestId,
        }));
        break;
    }
    response.set('Retry-After', '1');
    sendError(response, 503, 'DEPENDENCY_UNAVAILABLE', 'Session verification is unavailable.');
  };
}
