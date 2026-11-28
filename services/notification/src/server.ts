import { createApp } from './app.js';
import { brokerConfig, ConsumerMetrics, startConsumer } from './consumer.js';
import { checkDependencies, createPool } from './dependencies.js';
import { createIdentityClient } from './identity.js';
import { processEvent } from './processing.js';
import { log } from './log.js';
import { createNotificationRoutes } from './routes.js';

const introspect = createIdentityClient(
  process.env.IDENTITY_BASE_URL,
  process.env.NOTIFICATION_IDENTITY_SERVICE_KEY,
);
const port = Number(process.env.PORT ?? '8002');
const host = process.env.HOST ?? '127.0.0.1';
const databaseUrl = process.env.DATABASE_URL;
const identityReadyUrl = process.env.IDENTITY_READY_URL;
const pool = databaseUrl ? createPool(databaseUrl) : undefined;
const broker = brokerConfig(process.env);
const consumerMetrics = new ConsumerMetrics();

pool?.on('error', () => {
  log('error', 'database_connection_error');
});

const app = createApp(async (requestId) => {
  if (!pool || !identityReadyUrl) throw new Error('Readiness dependencies are not configured');
  await checkDependencies(pool, identityReadyUrl, requestId);
}, {
  introspect,
  routes: createNotificationRoutes(pool, process.env.NOTIFICATION_CURSOR_SECRET),
}, () => consumerMetrics.lines());

const server = app.listen(port, host, () => {
  log('info', 'listening', { host, port });
});

const consumer = pool && broker
  ? startConsumer(broker, (content) => processEvent(pool, content), consumerMetrics)
  : undefined;
if (!consumer) {
  log('warn', 'consumer_disabled');
}

process.once('SIGTERM', async () => {
  server.close();
  await consumer?.stop();
  await pool?.end();
});
