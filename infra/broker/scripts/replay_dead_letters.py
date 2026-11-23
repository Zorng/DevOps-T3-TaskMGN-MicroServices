#!/usr/bin/env python3
"""Replay dead-lettered messages from <queue>.dlq back to <queue>.

Operator tool: run it with the broker admin account only (service accounts have
no access to dead-letter queues). Each message keeps its body, message_id (the
event ID), type, headers and content type, so the consumer's deduplication on
event ID makes a replay safe. A message leaves the dead-letter queue only after
the broker has confirmed it is back on the main queue.

Usage:
  python scripts/replay_dead_letters.py notifications.v1 --dry-run
  python scripts/replay_dead_letters.py notifications.v1 --limit 10

Connection settings come from the same BROKER_* environment variables as the rest
of this folder (see ../.env.example).
"""
import argparse
import os
import sys

import pika

QUEUES = ("notifications.v1", "task-cleanup.v1")
# Headers the broker adds when it dead-letters a message; do not copy them back.
BROKER_HEADER_PREFIXES = ("x-death", "x-first-death", "x-last-death")


def connection_parameters():
    credentials = pika.PlainCredentials(
        os.environ["BROKER_ADMIN_USER"], os.environ["BROKER_ADMIN_PASSWORD"]
    )
    return pika.ConnectionParameters(
        host=os.environ.get("BROKER_HOST", "127.0.0.1"),
        port=int(os.environ.get("BROKER_AMQP_PORT", "5672")),
        virtual_host=os.environ.get("BROKER_VHOST", "taskmgn"),
        credentials=credentials,
    )


def replay(queue, parameters=None, limit=None, dry_run=False):
    """Move up to `limit` messages from <queue>.dlq to <queue>. Returns the count."""
    if queue not in QUEUES:
        raise ValueError(f"unknown queue {queue!r}; expected one of {QUEUES}")
    dlq = f"{queue}.dlq"
    connection = pika.BlockingConnection(parameters or connection_parameters())
    try:
        channel = connection.channel()
        channel.confirm_delivery()
        moved = 0
        while limit is None or moved < limit:
            method, props, body = channel.basic_get(dlq, auto_ack=False)
            if method is None:
                break
            if dry_run:
                # Put it back untouched and stop: a dry run must not loop forever.
                channel.basic_nack(method.delivery_tag, requeue=True)
                print(f"[dry-run] {dlq} has messages; message_id={props.message_id}")
                break
            headers = {
                key: value
                for key, value in (props.headers or {}).items()
                if not key.startswith(BROKER_HEADER_PREFIXES)
            }
            republished = pika.BasicProperties(
                delivery_mode=2,
                content_type=props.content_type,
                content_encoding=props.content_encoding,
                message_id=props.message_id,
                correlation_id=props.correlation_id,
                type=props.type,
                timestamp=props.timestamp,
                headers=headers or None,
            )
            # retry-return routes by main-queue name, straight back to the queue.
            channel.basic_publish("retry-return", queue, body, republished, mandatory=True)
            channel.basic_ack(method.delivery_tag)  # only after the confirm above
            moved += 1
            print(f"replayed message_id={props.message_id} to {queue}")
        return moved
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("queue", choices=QUEUES)
    parser.add_argument("--limit", type=int, default=None, help="maximum messages to replay")
    parser.add_argument("--dry-run", action="store_true", help="report without moving anything")
    args = parser.parse_args()
    count = replay(args.queue, limit=args.limit, dry_run=args.dry_run)
    print(f"{count} message(s) replayed")


if __name__ == "__main__":
    sys.exit(main())
