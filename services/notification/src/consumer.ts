import { setTimeout as sleep } from 'node:timers/promises';
import { connect, type ChannelModel, type ConfirmChannel, type ConsumeMessage } from 'amqplib';
import { InvalidEventError } from './events.js';
import { uuidPattern } from './http.js';
import type { ProcessResult } from './processing.js';

export const queue = 'notifications.v1';
const retryDelays = ['5s', '30s', '2m', '10m', '30m'];
const maxAttempts = retryDelays.length + 1;
const eventTypes = new Set(['invitation.created.v1', 'task.assigned.v1', 'task.comment_mentioned.v1']);

export type BrokerConfig = {
  host: string;
  port: number;
  vhost: string;
  username: string;
  password: string;
  tls: boolean;
};

export type ProcessMessage = (content: Buffer) => Promise<ProcessResult>;
type Log = (event: string, fields?: Record<string, unknown>) => void;
type Options = { reconnectDelayMs?: number; confirmTimeoutMs?: number; log?: Log };

export function brokerConfig(env: NodeJS.ProcessEnv): BrokerConfig | undefined {
  if (!env.BROKER_HOST || !env.BROKER_PASSWORD) return;
  return {
    host: env.BROKER_HOST,
    port: Number(env.BROKER_PORT ?? '5672'),
    vhost: env.BROKER_VHOST ?? 'taskmgn',
    username: env.BROKER_USERNAME ?? 'notification',
    password: env.BROKER_PASSWORD,
    tls: env.BROKER_TLS === 'true',
  };
}

export class ConsumerMetrics {
  processed: Record<ProcessResult, number> = { created: 0, suppressed: 0, duplicate: 0 };
  invalid = 0;
  failures = 0;
  retries = 0;
  deadLetters = 0;
  disconnects = 0;

  lines() {
    return [
      '# TYPE notification_consumer_events_total counter',
      ...Object.entries(this.processed)
        .map(([outcome, count]) => `notification_consumer_events_total{outcome="${outcome}"} ${count}`),
      ...[
        ['invalid_events_total', this.invalid],
        ['processing_failures_total', this.failures],
        ['retries_total', this.retries],
        ['dead_letters_total', this.deadLetters],
        ['disconnects_total', this.disconnects],
      ].flatMap(([name, count]) => [`# TYPE notification_consumer_${name} counter`, `notification_consumer_${name} ${count}`]),
    ];
  }
}

export function startConsumer(
  config: BrokerConfig,
  processMessage: ProcessMessage,
  metrics: ConsumerMetrics,
  { reconnectDelayMs = 5000, confirmTimeoutMs = 5000, log = logJson }: Options = {},
) {
  let stopped = false;
  let model: ChannelModel | undefined;

  const run = async () => {
    while (!stopped) {
      try {
        model = await connect({
          protocol: config.tls ? 'amqps' : 'amqp',
          hostname: config.host,
          port: config.port,
          vhost: config.vhost,
          username: config.username,
          password: config.password,
          heartbeat: 30,
        }, { timeout: 5000 });
        await consume(model, processMessage, metrics, confirmTimeoutMs, log);
      } catch {
        await model?.close().catch(() => {});
      }
      model = undefined;
      if (stopped) break;
      metrics.disconnects++;
      log('consumer_disconnected', { retry_in_ms: reconnectDelayMs });
      await sleep(reconnectDelayMs);
    }
  };
  const done = run();

  return {
    done,
    async stop() {
      stopped = true;
      await model?.close().catch(() => {});
      await done;
    },
  };
}

async function consume(
  model: ChannelModel,
  processMessage: ProcessMessage,
  metrics: ConsumerMetrics,
  confirmTimeoutMs: number,
  log: Log,
) {
  let open = true;
  const closed = new Promise<void>((resolve) => model.once('close', () => {
    open = false;
    resolve();
  }));
  model.on('error', () => {});
  const channel = await model.createConfirmChannel();
  channel.on('error', () => {});
  await channel.prefetch(10);

  // Deliveries are handled one at a time so a returned forward belongs to the current message.
  let pending = Promise.resolve();
  await channel.consume(queue, (message) => {
    if (!message) {
      model.close().catch(() => {});
      return;
    }
    pending = pending
      .then(() => open ? handleDelivery(channel, message, processMessage, metrics, { confirmTimeoutMs, log }) : undefined)
      .catch(() => model.close().catch(() => {}));
  });
  log('consumer_connected', { queue });
  await closed;
}

export async function handleDelivery(
  channel: ConfirmChannel,
  message: ConsumeMessage,
  processMessage: ProcessMessage,
  metrics: ConsumerMetrics,
  { confirmTimeoutMs = 5000, log = logJson }: { confirmTimeoutMs?: number; log?: Log } = {},
) {
  const context = logContext(message);
  const attempt = attemptOf(message);
  if (attempt === undefined) {
    metrics.invalid++;
    await deadLetter('invalid_attempt_header', 1);
    return;
  }

  let result: ProcessResult;
  try {
    result = await processMessage(message.content);
  } catch (error) {
    if (error instanceof InvalidEventError) {
      metrics.invalid++;
      await deadLetter('invalid_event', attempt);
      return;
    }
    metrics.failures++;
    if (attempt >= maxAttempts) {
      await deadLetter('retries_exhausted', attempt);
      return;
    }
    const delay = retryDelays[attempt - 1];
    await forward(channel, message, 'retry', `${queue}.retry.${delay}`, attempt + 1, confirmTimeoutMs);
    channel.ack(message);
    metrics.retries++;
    log('event_retry_scheduled', { ...context, attempt, delay });
    return;
  }

  channel.ack(message);
  metrics.processed[result]++;
  log('event_processed', { ...context, attempt, outcome: result });

  async function deadLetter(reason: string, failedAttempt: number) {
    await forward(channel, message, 'dead-letter', queue, failedAttempt, confirmTimeoutMs);
    channel.ack(message);
    metrics.deadLetters++;
    log('event_dead_lettered', { ...context, attempt: failedAttempt, reason });
  }
}

async function forward(
  channel: ConfirmChannel,
  message: ConsumeMessage,
  exchange: string,
  routingKey: string,
  attempt: number,
  confirmTimeoutMs: number,
) {
  let returned = false;
  const onReturn = () => { returned = true; };
  channel.on('return', onReturn);
  try {
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Broker did not confirm the forward')), confirmTimeoutMs);
      const { properties } = message;
      channel.publish(exchange, routingKey, message.content, {
        persistent: true,
        mandatory: true,
        contentType: properties.contentType ?? 'application/json',
        messageId: properties.messageId,
        type: properties.type,
        headers: { ...properties.headers, 'x-attempt': attempt },
      }, (error) => {
        clearTimeout(timer);
        if (error) reject(error);
        else resolve();
      });
    });
  } finally {
    channel.off('return', onReturn);
  }
  if (returned) throw new Error('Broker could not route the forward');
}

function attemptOf(message: ConsumeMessage) {
  const value: unknown = message.properties.headers?.['x-attempt'];
  if (value === undefined) return 1;
  if (typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= maxAttempts) return value;
}

function logContext(message: ConsumeMessage) {
  const { messageId, type } = message.properties;
  let requestId: unknown;
  try {
    requestId = JSON.parse(message.content.toString('utf8')).request_id;
  } catch {}
  return {
    event_id: typeof messageId === 'string' && uuidPattern.test(messageId) ? messageId : null,
    request_id: typeof requestId === 'string' && uuidPattern.test(requestId) ? requestId : null,
    type: typeof type === 'string' && eventTypes.has(type) ? type : null,
  };
}

function logJson(event: string, fields: Record<string, unknown> = {}) {
  console.log(JSON.stringify({ timestamp: new Date().toISOString(), service: 'notification', event, ...fields }));
}
