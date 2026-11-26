import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import schema from './contracts/event.schema.json';

export type NotificationRecord = {
  eventId: string;
  recipientId: string;
  type: 'invitation' | 'assignment' | 'mention';
  actorId: string;
  actorDisplayName: string;
  workspaceId: string;
  invitationId: string | null;
  taskId: string | null;
  commentId: string | null;
  occurredAt: string;
};

type Actor = { id: string; display_name: string };
type Envelope<Type, Data> = {
  id: string;
  type: Type;
  aggregate_id: string;
  occurred_at: string;
  data: Data & { workspace_id: string; actor: Actor; recipient_id: string };
};
type NotificationEvent =
  | Envelope<'invitation.created.v1', { invitation_id: string }>
  | Envelope<'task.assigned.v1', { task_id: string }>
  | Envelope<'task.comment_mentioned.v1', { task_id: string; comment_id: string }>;

export class InvalidEventError extends Error {}

const maxMessageBytes = 64 * 1024;
const ajv = new Ajv2020({ strict: true });
addFormats(ajv);
const validateEnvelope = ajv.compile(schema);

export function parseEvent(message: Buffer | string): NotificationRecord {
  const raw = typeof message === 'string' ? Buffer.from(message) : message;
  if (raw.length > maxMessageBytes) throw new InvalidEventError('Event exceeds 64 KiB');

  let event: unknown;
  try {
    event = JSON.parse(raw.toString('utf8'));
  } catch {
    throw new InvalidEventError('Event is not valid JSON');
  }
  if (!validateEnvelope(event)) throw new InvalidEventError('Event does not match the contract schema');

  const record = toRecord(event as NotificationEvent);
  if (!record) throw new InvalidEventError('Event type is not handled by Notification');
  return record;
}

function toRecord(event: NotificationEvent): NotificationRecord | undefined {
  const { data } = event;
  const base = {
    eventId: event.id,
    recipientId: data.recipient_id,
    actorId: data.actor.id,
    actorDisplayName: data.actor.display_name,
    workspaceId: data.workspace_id,
    occurredAt: event.occurred_at,
  };
  switch (event.type) {
    case 'invitation.created.v1':
      requireAggregate(event, event.data.invitation_id);
      return { ...base, type: 'invitation', invitationId: event.data.invitation_id, taskId: null, commentId: null };
    case 'task.assigned.v1':
      requireAggregate(event, event.data.task_id);
      return { ...base, type: 'assignment', invitationId: null, taskId: event.data.task_id, commentId: null };
    case 'task.comment_mentioned.v1':
      requireAggregate(event, event.data.comment_id);
      return {
        ...base, type: 'mention', invitationId: null, taskId: event.data.task_id, commentId: event.data.comment_id,
      };
  }
}

function requireAggregate(event: NotificationEvent, expected: string) {
  if (event.aggregate_id !== expected) throw new InvalidEventError('Event aggregate does not match its data');
}
