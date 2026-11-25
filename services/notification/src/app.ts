import { randomUUID } from 'node:crypto';
import express, { type ErrorRequestHandler, type RequestHandler, type Router } from 'express';
import { liveness, readiness } from './health.js';
import { requireSession } from './authorization.js';
import { sendError, uuidPattern } from './http.js';
import type { IntrospectSession } from './identity.js';
const bodyLimit = 64 * 1024;

export function createApp(
  checkDependencies: (requestId: string) => Promise<void>,
  sessions?: { introspect: IntrospectSession; routes?: Router },
) {
  const app = express();
  app.disable('x-powered-by');
  app.disable('etag');
  app.enable('strict routing');
  app.enable('case sensitive routing');
  let completedRequests = 0;
  let failedRequests = 0;
  let durationSeconds = 0;

  app.use((request, response, next) => {
    const suppliedId = request.get('X-Request-ID') ?? '';
    const requestId = uuidPattern.test(suppliedId) ? suppliedId : randomUUID();
    const started = performance.now();
    response.set('X-Request-ID', requestId);
    response.set('Cache-Control', 'no-store');
    response.on('finish', () => {
      const durationMs = performance.now() - started;
      completedRequests++;
      if (response.statusCode >= 500) failedRequests++;
      durationSeconds += durationMs / 1000;
      // Only log allowlisted fields. Raw paths, headers, bodies and errors may contain secrets.
      console.log(JSON.stringify({
        timestamp: new Date().toISOString(),
        service: 'notification',
        event: 'request_completed',
        request_id: requestId,
        status: response.statusCode,
        duration_ms: Math.round(durationMs),
      }));
    });
    next();
  });

  app.use(express.raw({ type: () => true, limit: bodyLimit, inflate: false }));
  app.use((request, response, next) => {
    if (!request.body?.length) return next();

    if (!request.is('application/json')) {
      sendError(response, 415, 'UNSUPPORTED_MEDIA_TYPE', 'Request bodies must use application/json.');
      return;
    }
    try {
      JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(request.body));
    } catch {
      sendError(response, 400, 'MALFORMED_REQUEST', 'The request body must be valid UTF-8 JSON.');
      return;
    }
    next();
  });

  app.get('/health/live', requireBodyless, (_request, response) => response.json(liveness()));
  app.get('/health/ready', requireBodyless, async (_request, response) => {
    const requestId = String(response.get('X-Request-ID'));
    const { code, body } = await readiness(() => checkDependencies(requestId));
    response.status(code).json(body);
  });
  app.get('/metrics', requireBodyless, (_request, response) => {
    response.type('text/plain').send([
      '# TYPE notification_http_requests_total counter',
      `notification_http_requests_total ${completedRequests}`,
      '# TYPE notification_http_server_errors_total counter',
      `notification_http_server_errors_total ${failedRequests}`,
      '# TYPE notification_http_request_duration_seconds_total counter',
      `notification_http_request_duration_seconds_total ${durationSeconds}`,
      '',
    ].join('\n'));
  });

  if (sessions?.routes) {
    app.use('/api/v1/notifications', requireSession(sessions.introspect), sessions.routes);
  }

  app.use((_request, response) => sendError(response, 404, 'NOT_FOUND', 'Route not found.'));
  const handleError: ErrorRequestHandler = (error, _request, response, next) => {
    if (response.headersSent) {
      next(error);
      return;
    }
    switch (error.type) {
      case 'entity.too.large':
        sendError(response, 413, 'PAYLOAD_TOO_LARGE', 'The request body exceeds 64 KiB.');
        break;
      case 'encoding.unsupported':
        sendError(response, 415, 'UNSUPPORTED_MEDIA_TYPE', 'The request content encoding is unsupported.');
        break;
      case 'request.size.invalid':
      case 'request.aborted':
        sendError(response, 400, 'MALFORMED_REQUEST', 'The request body is incomplete.');
        break;
      default:
        sendError(response, 500, 'INTERNAL_ERROR', 'An unexpected error occurred.');
    }
  };
  app.use(handleError);
  return app;
}

const requireBodyless: RequestHandler = (request, response, next) => {
  if (request.body?.length || Object.keys(request.query).length) {
    sendError(response, 400, 'MALFORMED_REQUEST', 'This endpoint accepts no body or query parameters.');
    return;
  }
  next();
};
