"""Run with python -m identity_workspace.outbox.worker (separate from HTTP)."""

import argparse
import logging
import random
import signal
from threading import Event

from prometheus_client import CollectorRegistry, Counter, Gauge, start_http_server
from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.config import ConfigurationError, load_settings
from identity_workspace.database import create_database
from identity_workspace.outbox.broker import PublishFailure, RabbitPublisher
from identity_workspace.outbox.events import InvalidEvent, encode_event
from identity_workspace.outbox.store import OutboxStore
from identity_workspace.structured_logging import ApplicationLogger


def retry_delay(attempt: int, jitter=random.random) -> float:
    base = min(60, 2 ** min(max(0, attempt - 1), 6))
    return min(60, base * (1 + 0.2 * jitter()))


class PublisherWorker:
    def __init__(self, store, publisher, *, level="INFO", stream=None, jitter=random.random):
        self.store, self.publisher, self.jitter = store, publisher, jitter
        self.registry = CollectorRegistry()
        self.published = Counter("identity_outbox_published_total", "Confirmed, recorded publications.", registry=self.registry)
        self.failures = Counter("identity_outbox_publish_failures_total", "Failed publication attempts.", ["reason"], registry=self.registry)
        self.retries = Counter("identity_outbox_retries_total", "Claims after the first delivery attempt.", registry=self.registry)
        self.pending = Gauge("identity_outbox_pending", "Unpublished records, including leased records.", registry=self.registry)
        self.age = Gauge("identity_outbox_oldest_pending_age_seconds", "Oldest unpublished record age.", registry=self.registry)
        self.storage = Gauge("identity_outbox_storage_available", "Last worker database observation: 1 available, 0 unavailable.", registry=self.registry)
        self.log_failures = Counter("identity_outbox_log_failures_total", "Log sink failures.", registry=self.registry)
        self.log = ApplicationLogger(level, lambda: set(), stream=stream, on_failure=self.log_failures.inc)
        self.last_failed = False

    def tick(self) -> bool:
        self.last_failed = False
        delivery = self.store.claim()
        if delivery is None:
            return False
        fields = {"event_id": str(delivery.event_id), "request_id": delivery.payload.get("request_id"),
                  "event_type": delivery.payload.get("type"), "attempt": delivery.attempt}
        if delivery.attempt > 1:
            self.retries.inc()
        try:
            body = encode_event(delivery.payload)
            if str(delivery.event_id) != delivery.payload["id"]:
                raise InvalidEvent("Mismatched event identity; content omitted.")
            self.publisher.publish(delivery.payload, body)
        except (PublishFailure, InvalidEvent) as error:
            self.last_failed = True
            reason = "invalid_event" if isinstance(error, InvalidEvent) else str(error)
            if reason not in {"invalid_event", "unroutable", "nack", "broker_unavailable"}:
                reason = "broker_unavailable"
            self.failures.labels(reason=reason).inc()
            delay = retry_delay(delivery.attempt, self.jitter)
            self.store.complete(delivery, delay=delay)
            self.log.event(logging.WARNING, "outbox_publish_failed", **fields, error_category=reason)
        else:
            if self.store.complete(delivery):
                self.published.inc()
                self.log.event(logging.INFO, "outbox_published", **fields)
        return True

    def observe(self):
        count, age = self.store.stats()
        self.pending.set(count)
        self.age.set(age)
        self.storage.set(1)
        if age > 300:
            self.log.event(logging.WARNING, "outbox_backlog_old")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one due event; failure exits nonzero.")
    parser.add_argument("--purge-confirmed", action="store_true", help="Delete up to 1000 records confirmed more than seven days ago.")
    parser.add_argument("--metrics-host", default="127.0.0.1")
    parser.add_argument("--metrics-port", type=int, default=8001)
    args = parser.parse_args()
    database = worker = server = None
    try:
        settings = load_settings()
        database = create_database(settings)
        store = OutboxStore(database)
        if args.purge_confirmed:
            print(f"Purged {store.purge_confirmed()} confirmed records; pending records retained.")
            return 0
        worker = PublisherWorker(store, RabbitPublisher(settings), level=settings.log_level)
        stop = Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        if not args.once:
            server, _ = start_http_server(args.metrics_port, addr=args.metrics_host, registry=worker.registry)
        while not stop.is_set():
            try:
                worked = worker.tick()
                worker.observe()
                if args.once:
                    return int(worker.last_failed)
            except SQLAlchemyError:
                worker.storage.set(0)
                worker.log.event(logging.ERROR, "outbox_storage_failed", error_category="storage_unavailable")
                if args.once:
                    return 1
                worked = False
            stop.wait(0.05 if worked else 1)
        return 0
    except ConfigurationError:
        print("Outbox configuration invalid; check database/broker settings. Values omitted.")
        return 2
    except Exception:
        print("Outbox worker stopped unexpectedly; configuration and diagnostics omitted.")
        return 1
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if worker is not None:
            worker.log.close()
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
