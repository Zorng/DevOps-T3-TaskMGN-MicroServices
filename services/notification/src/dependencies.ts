import { Pool } from 'pg';

export function createPool(connectionString: string) {
  return new Pool({
    connectionString,
    connectionTimeoutMillis: 1500,
    query_timeout: 1500,
    statement_timeout: 1500,
  });
}

export async function checkDependencies(pool: Pool, identityReadyUrl: string, requestId: string) {
  await Promise.all([
    pool.query('SELECT 1 FROM notification_service.notifications LIMIT 1'),
    (async () => {
      const response = await fetch(identityReadyUrl, {
        signal: AbortSignal.timeout(1500),
        redirect: 'error',
        headers: { 'X-Request-ID': requestId },
      });
      await response.body?.cancel();
      if (response.status !== 200) throw new Error('Identity is unavailable');
    })(),
  ]);
}
