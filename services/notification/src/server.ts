import { createApp } from './app.js';
import { checkDependencies, createPool } from './dependencies.js';

const port = Number(process.env.PORT ?? '8002');
const host = process.env.HOST ?? '127.0.0.1';
const databaseUrl = process.env.DATABASE_URL;
const identityReadyUrl = process.env.IDENTITY_READY_URL;
const pool = databaseUrl ? createPool(databaseUrl) : undefined;

pool?.on('error', () => {
  console.error(JSON.stringify({ service: 'notification', event: 'database_connection_error' }));
});

const app = createApp(async (requestId) => {
  if (!pool || !identityReadyUrl) throw new Error('Readiness dependencies are not configured');
  await checkDependencies(pool, identityReadyUrl, requestId);
});

app.listen(port, host, () => {
  console.log(JSON.stringify({ service: 'notification', event: 'listening', host, port }));
});
