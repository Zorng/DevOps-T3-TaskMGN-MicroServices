import { uuidPattern } from './http.js';

type SessionResult =
  | { kind: 'verified'; recipientId: string }
  | { kind: 'invalid' }
  | { kind: 'credential_rejected' }
  | { kind: 'unavailable' };

export type IntrospectSession = (bearer: string, requestId: string) => Promise<SessionResult>;

const timeoutMs = 2000;
const maxAttempts = 2;

export function createIdentityClient(baseUrl?: string, serviceKey?: string): IntrospectSession {
  const endpoint = introspectionEndpoint(baseUrl);
  return async (bearer, requestId) => {
    if (!endpoint || !serviceKey) return { kind: 'unavailable' };

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      let response: Response;
      try {
        response = await fetch(endpoint, {
          method: 'POST',
          redirect: 'manual',
          signal: AbortSignal.timeout(timeoutMs),
          headers: {
            Authorization: bearer,
            'X-Service-Key': serviceKey,
            'X-Request-ID': requestId,
          },
        });
      } catch {
        continue;
      }

      if (response.status === 200 || response.status === 401) {
        const body: unknown = await response.json().catch(() => undefined);
        return response.status === 200 ? verifiedSession(body) : rejectedSession(body);
      }
      await response.body?.cancel().catch(() => {});
      if (response.status === 403) return { kind: 'credential_rejected' };
      if (response.status !== 503) break;
    }
    return { kind: 'unavailable' };
  };
}

function introspectionEndpoint(baseUrl?: string) {
  if (!baseUrl) return;
  try {
    const url = new URL(baseUrl);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
        url.pathname !== '/' || url.search || url.hash) return;
    return new URL('/internal/v1/auth/introspect', url);
  } catch {
    return;
  }
}

function verifiedSession(body: unknown): SessionResult {
  if (isRecord(body) && isRecord(body.user) &&
      typeof body.user.id === 'string' && uuidPattern.test(body.user.id) &&
      typeof body.user.display_name === 'string' && body.user.display_name.length > 0 &&
      typeof body.session_expires_at === 'string' && Number.isFinite(Date.parse(body.session_expires_at))) {
    return { kind: 'verified', recipientId: body.user.id };
  }
  return { kind: 'unavailable' };
}

function rejectedSession(body: unknown): SessionResult {
  if (!isRecord(body) || !isRecord(body.error)) return { kind: 'unavailable' };
  switch (body.error.code) {
    case 'AUTH_REQUIRED':
    case 'SESSION_INVALID':
      return { kind: 'invalid' };
    case 'SERVICE_CREDENTIAL_INVALID':
      return { kind: 'credential_rejected' };
    default:
      return { kind: 'unavailable' };
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
