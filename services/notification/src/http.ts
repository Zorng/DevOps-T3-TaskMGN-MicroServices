import type { Response } from 'express';

export const uuidPattern = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;

export function sendError(response: Response, status: number, code: string, message: string) {
  response.status(status).json({
    error: { code, message, request_id: response.get('X-Request-ID'), details: [] },
  });
}
