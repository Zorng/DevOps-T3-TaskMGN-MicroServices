"""Receive smoke-test events from disposable queues; not an application consumer.

The host smoke script passes this file to Python inside a one-off service image.
Only that probe receives the generated read-only broker credential file.
"""

import json
import sys
import time

QUEUES = ("notifications.v1", "task-cleanup.v1")


class DeliveryCheckFailure(RuntimeError):
    def __init__(self, reason, summary):
        super().__init__(reason)
        self.summary = summary


def verify_delivery(expected, fetch, acknowledge, *, timeout=30, clock=time.monotonic, pause=time.sleep):
    """Check exact IDs/bytes/headers on their expected queues; accept duplicates.

    expected maps queue -> event ID -> (canonical bytes, request ID).
    fetch uses basic_get with manual acknowledgements. A malformed/unexpected
    message is left unacknowledged and this disposable smoke check fails.
    """
    if set(expected) != set(QUEUES) or not all(expected[queue] for queue in QUEUES):
        raise ValueError("Both logical queues need a nonempty expected event set.")
    observed = {queue: set() for queue in QUEUES}
    deliveries = duplicates = 0
    deadline = clock() + timeout

    def summary():
        return {
            "unique_notifications": len(observed[QUEUES[0]]),
            "unique_cleanup": len(observed[QUEUES[1]]),
            "deliveries": deliveries, "duplicate_deliveries": duplicates,
        }

    while clock() < deadline:
        for queue in QUEUES:
            # Bounded batches keep a stream of duplicates from starving the
            # other queue or defeating the overall deadline.
            for _ in range(64):
                if clock() >= deadline:
                    break
                method, properties, body = fetch(queue)
                if method is None:
                    break
                identifier = properties.message_id
                if identifier not in expected[queue]:
                    raise DeliveryCheckFailure("unexpected_event_or_route", summary())
                canonical, request_id = expected[queue][identifier]
                if (body != canonical or properties.delivery_mode != 2
                    or properties.content_type != "application/json"
                    or properties.content_encoding != "utf-8"
                    or properties.correlation_id != request_id
                    or properties.app_id != "identity-workspace"
                    or properties.type != json.loads(canonical)["type"]):
                    raise DeliveryCheckFailure("event_payload_or_headers_differ", summary())
                deliveries += 1
                duplicates += int(identifier in observed[queue])
                observed[queue].add(identifier)
                acknowledge(method.delivery_tag)
        if all(observed[queue] == set(expected[queue]) for queue in QUEUES):
            return summary()
        pause(min(0.25, max(0, deadline - clock())))
    raise DeliveryCheckFailure("expected_event_delivery_timeout", summary())


def main():
    # Imports live inside main so pure unit tests use no database or broker.
    import logging
    from pathlib import Path

    import pika
    from sqlalchemy import select

    from identity_workspace.config import load_settings
    from identity_workspace.database import create_database
    from identity_workspace.outbox.events import ROUTES, encode_event
    from identity_workspace.persistence import outbox

    database = None
    try:
        expected = {queue: {} for queue in QUEUES}
        database = create_database(load_settings())
        with database.engine.connect() as connection:
            rows = connection.execute(select(outbox.c.payload, outbox.c.published_at)).all()
        for payload, published_at in rows:
            if published_at is None:
                raise ValueError("Publication has not completed.")
            body = encode_event(payload)
            expected[ROUTES[payload["type"]]][payload["id"]] = (body, payload["request_id"])
        if len(rows) != 8 or [len(expected[q]) for q in QUEUES] != [5, 3]:
            raise ValueError("The disposable smoke fixture must contain eight expected events.")
        # Pika diagnostics may contain connection details. Only closed probe
        # summaries are emitted, following the production adapter boundary.
        logger = logging.getLogger("pika")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        with Path("/run/secrets/identity_broker_probe_url").open("r", encoding="utf-8") as source:
            url = source.read(4097).strip()
        if len(url) > 4096:
            raise ValueError("Broker probe URL exceeds its bound.")
        parameters = pika.URLParameters(url)
        parameters.socket_timeout, parameters.stack_timeout = 3, 8
        parameters.heartbeat, parameters.blocked_connection_timeout = 5, 5
        parameters.connection_attempts = 1
        with pika.BlockingConnection(parameters) as connection:
            def timeout():
                raise TimeoutError()

            timer = connection.call_later(35, timeout)
            channel = connection.channel()
            result = verify_delivery(expected, lambda queue: channel.basic_get(queue, auto_ack=False), channel.basic_ack)
            connection.remove_timeout(timer)
        print(json.dumps({"status": "passed", **result}))
        return 0
    except DeliveryCheckFailure as error:
        print(json.dumps({"status": "failed", "reason": str(error), **error.summary}), file=sys.stderr)
        return 1
    except Exception:
        # Do not serialize provider/DB errors, credentials, bodies or headers.
        print('{"status":"failed","reason":"delivery_probe_unavailable"}', file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
