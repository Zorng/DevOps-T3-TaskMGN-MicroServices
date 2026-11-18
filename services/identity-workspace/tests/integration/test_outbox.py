"""Actual PostgreSQL + durable RabbitMQ checks; probes are not owning consumers."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
from io import StringIO
import json
import os
from pathlib import Path
import runpy
import subprocess
import time
from unittest.mock import Mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pika
from pydantic import SecretStr
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError

from identity_workspace.auth.store import database_now
from identity_workspace.main import create_app
from identity_workspace.outbox.broker import PublishFailure, RabbitPublisher
from identity_workspace.outbox.events import append_event, encode_event
from identity_workspace.outbox.store import LEASE_SECONDS, OutboxStore
from identity_workspace.outbox.worker import PublisherWorker
from identity_workspace.persistence import outbox
from tests.workspace_contract import event_contract

SERVICE = Path(__file__).resolve().parents[2]
CONTRACTS = SERVICE.parents[1] / "api_contracts"


@dataclass
class Broker:
    args: list[str]
    environment: dict = field(repr=False)
    directory: Path = field(repr=False)
    port: int = 0

    def compose(self, *args):
        result = subprocess.run([*self.args, *args], env=self.environment, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("Disposable broker command failed; no credential values printed: " + result.stderr[-1500:])
        return result.stdout.strip()

    def url(self, name="broker-url"):
        return (self.directory / name).read_text(encoding="utf-8").strip().replace("@broker:5672/", f"@127.0.0.1:{self.port}/")

    def settings(self, settings):
        return settings.model_copy(update={"broker_url": SecretStr(self.url()), "broker_url_file": None})

    def probe(self):
        parameters = pika.URLParameters(self.url("broker-probe-url"))
        parameters.socket_timeout, parameters.stack_timeout, parameters.blocked_connection_timeout = 3, 8, 5
        return pika.BlockingConnection(parameters)

    def drain(self):
        with self.probe() as connection:
            channel = connection.channel()
            for queue in ("notifications.v1", "task-cleanup.v1", "notifications.v1.dead", "task-cleanup.v1.dead"):
                while True:
                    method, _, _ = channel.basic_get(queue, auto_ack=True)
                    if method is None:
                        break


@pytest.fixture(scope="session")
def rabbitmq(tmp_path_factory, postgres):
    directory = tmp_path_factory.mktemp("identity-broker")
    runpy.run_path(str(SERVICE / "scripts/setup_broker.py"))["setup"](directory)
    env_file = directory / "compose.env"
    env_file.write_text("", encoding="utf-8")
    environment = dict(postgres.environment)
    environment.update(IDENTITY_BROKER_SECRETS_DIR=str(directory), IDENTITY_BROKER_PORT="0")
    broker = Broker([
        "docker", "compose", "--file", str(SERVICE / "compose.yaml"), "--file", str(SERVICE / "compose.outbox.yaml"),
        "--project-name", "identity-outbox-it-" + uuid4().hex[:12], "--env-file", str(env_file), "--profile", "outbox",
    ], environment, directory)
    # Suppress broker-library diagnostics through the same production boundary.
    RabbitPublisher(postgres.settings().model_copy(update={"broker_url": SecretStr("amqp://example:synthetic@localhost/identity")}))
    try:
        broker.compose("up", "--detach", "--wait", "--wait-timeout", "100", "broker")
        broker.port = int(broker.compose("port", "broker", "5672").rsplit(":", 1)[1])
        broker.environment["IDENTITY_BROKER_PORT"] = str(broker.port)
        deadline = time.monotonic() + 30
        while True:
            try:
                broker.drain()
                break
            except pika.exceptions.AMQPError:
                if time.monotonic() > deadline:
                    raise RuntimeError("Disposable broker did not import its test topology.") from None
                time.sleep(0.5)
        yield broker
    finally:
        broker.compose("down", "--volumes", "--remove-orphans")


@pytest.fixture
def broker(rabbitmq):
    rabbitmq.drain()
    yield rabbitmq
    rabbitmq.drain()


@pytest.fixture
def payload():
    value = deepcopy(json.loads((CONTRACTS / "examples/events.json").read_text(encoding="utf-8"))[0])
    value["id"] = str(uuid4())
    return value


def insert(database, payload, *, age=0):
    with database.transaction() as session:
        now = database_now(session) - timedelta(seconds=age)
        append_event(session, payload, created_at=now)
    return UUID(payload["id"])


def stored(database, identifier):
    with database.transaction() as session:
        return session.execute(select(outbox).where(outbox.c.event_id == identifier)).mappings().one()


def due(database, identifier):
    with database.transaction() as session:
        session.execute(update(outbox).where(outbox.c.event_id == identifier).values(next_attempt_at=database_now(session) - timedelta(seconds=1)))


def test_claim_commits_lease_before_network_and_parallel_workers_are_distinct(database, payload):
    identifiers = set()
    for _ in range(8):
        value = deepcopy(payload)
        value["id"] = str(uuid4())
        identifiers.add(insert(database, value))
    store = OutboxStore(database)
    with ThreadPoolExecutor(max_workers=4) as executor:
        deliveries = list(executor.map(lambda _: store.claim(), range(8)))
    assert {d.event_id for d in deliveries} == identifiers
    assert all(d.attempt == 1 for d in deliveries) and store.claim() is None
    row = stored(database, deliveries[0].event_id)
    assert row["attempts"] == 1 and row["published_at"] is None
    assert (row["next_attempt_at"] - row["created_at"]).total_seconds() >= LEASE_SECONDS


def test_restart_after_claim_recovers_with_fence_and_same_payload(database, payload):
    identifier = insert(database, payload)
    first = OutboxStore(database).claim()
    assert OutboxStore(database).claim() is None
    due(database, identifier)  # Deterministically advance the lease; no two-minute sleep.
    second = OutboxStore(database).claim()
    assert first.event_id == second.event_id and first.payload == second.payload
    assert second.attempt == first.attempt + 1
    assert OutboxStore(database).complete(first) is False
    assert OutboxStore(database).complete(first, delay=1) is False
    assert stored(database, identifier)["published_at"] is None
    assert OutboxStore(database).complete(second) is True


def test_claim_and_sent_update_roll_back_on_database_failure(database, payload):
    identifier = insert(database, payload)

    def reject_update(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE identity_workspace.outbox"):
            raise OperationalError("Injected update failure", None, None)

    event.listen(database.engine, "before_cursor_execute", reject_update)
    try:
        with pytest.raises(OperationalError):
            OutboxStore(database).claim()
    finally:
        event.remove(database.engine, "before_cursor_execute", reject_update)
    assert stored(database, identifier)["attempts"] == 0


def test_failed_publisher_survives_new_worker_and_keeps_event_id(database, payload):
    identifier = insert(database, payload)
    store, failed = OutboxStore(database), Mock()
    failed.publish.side_effect = PublishFailure("broker_unavailable")
    worker = PublisherWorker(store, failed, stream=StringIO(), jitter=lambda: 0)
    assert worker.tick()
    row = stored(database, identifier)
    assert row["published_at"] is None and row["payload"] == payload and row["attempts"] == 1
    assert not worker.tick()
    due(database, identifier)
    recovered = Mock()
    assert PublisherWorker(OutboxStore(database), recovered, stream=StringIO()).tick()
    assert recovered.publish.call_args.args == (payload, encode_event(payload))
    assert stored(database, identifier)["published_at"] is not None


def test_retention_never_purges_pending_or_recent_confirmed_records(database, payload):
    pending = insert(database, payload, age=10 * 86400)
    identifiers = []
    for days in (6, 8):
        value = deepcopy(payload)
        value["id"] = str(uuid4())
        identifier = insert(database, value, age=10 * 86400)
        with database.transaction() as session:
            session.execute(update(outbox).where(outbox.c.event_id == identifier).values(published_at=database_now(session) - timedelta(days=days)))
        identifiers.append(identifier)
    assert OutboxStore(database).purge_confirmed() == 1
    assert stored(database, pending)["published_at"] is None
    assert stored(database, identifiers[0])["published_at"] is not None
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(outbox)) == 2


def test_real_confirm_persists_and_routes_event_with_safe_headers(database, broker, postgres, payload):
    identifier = insert(database, payload)
    worker = PublisherWorker(OutboxStore(database), RabbitPublisher(broker.settings(postgres.settings())), stream=StringIO())
    assert worker.tick() and not worker.last_failed
    assert stored(database, identifier)["published_at"] is not None
    with broker.probe() as connection:
        channel = connection.channel()
        method, properties, body = channel.basic_get("notifications.v1", auto_ack=False)
        assert method is not None and properties.delivery_mode == 2
        assert properties.message_id == payload["id"] and properties.correlation_id == payload["request_id"]
        assert json.loads(body) == payload and body == encode_event(payload)
        event_contract(json.loads(body))
        assert channel.basic_get("task-cleanup.v1", auto_ack=True)[0] is None
        channel.basic_ack(method.delivery_tag)


def test_unroutable_confirmation_never_marks_sent_and_recovers(database, broker, postgres, payload):
    identifier = insert(database, payload)
    # Keep envelope valid; remove its binding through the broker's local admin CLI.
    # Deleting/recreating the empty logical queue emulates missing provisioning.
    broker.compose("exec", "-T", "broker", "rabbitmqctl", "delete_queue", "--vhost", "identity", "notifications.v1")
    try:
        worker = PublisherWorker(OutboxStore(database), RabbitPublisher(broker.settings(postgres.settings())), stream=StringIO())
        assert worker.tick() and worker.last_failed
        assert worker.failures.labels(reason="unroutable")._value.get() == 1
        assert stored(database, identifier)["published_at"] is None
    finally:
        broker.compose("exec", "-T", "broker", "rabbitmqctl", "import_definitions", "/run/secrets/broker_definitions")
    due(database, identifier)
    assert worker.tick() and not worker.last_failed
    assert stored(database, identifier)["published_at"] is not None


def test_real_broker_outage_then_restart_recovers_pending(database, broker, postgres, payload):
    identifier = insert(database, payload)
    worker = PublisherWorker(OutboxStore(database), RabbitPublisher(broker.settings(postgres.settings())), stream=StringIO())
    broker.compose("stop", "broker")
    try:
        assert worker.tick() and worker.last_failed
        assert stored(database, identifier)["published_at"] is None
        assert database.check_readiness()  # Broker outage is independent of authority.
    finally:
        broker.compose("up", "--detach", "--wait", "--wait-timeout", "100", "broker")
    due(database, identifier)
    assert PublisherWorker(OutboxStore(database), RabbitPublisher(broker.settings(postgres.settings())), stream=StringIO()).tick()
    assert stored(database, identifier)["published_at"] is not None


def test_confirm_then_crash_duplicates_identical_id_and_payload(database, broker, postgres, payload):
    identifier = insert(database, payload)
    store = OutboxStore(database)
    first = store.claim()
    RabbitPublisher(broker.settings(postgres.settings())).publish(first.payload, encode_event(first.payload))
    # Crash window: positive confirm received, complete() deliberately never called.
    assert stored(database, identifier)["published_at"] is None
    due(database, identifier)
    assert PublisherWorker(OutboxStore(database), RabbitPublisher(broker.settings(postgres.settings())), stream=StringIO()).tick()
    with broker.probe() as connection:
        channel = connection.channel()
        received = [channel.basic_get("notifications.v1", auto_ack=True) for _ in range(2)]
        assert all(method is not None for method, _, _ in received)
        assert [props.message_id for _, props, _ in received] == [payload["id"]] * 2
        assert received[0][2] == received[1][2] == encode_event(payload)
    assert stored(database, identifier)["attempts"] == 2


def test_confirmed_message_survives_broker_restart(broker, postgres, payload):
    RabbitPublisher(broker.settings(postgres.settings())).publish(payload, encode_event(payload))
    broker.compose("restart", "broker")
    broker.compose("up", "--detach", "--wait", "--wait-timeout", "100", "broker")
    with broker.probe() as connection:
        method, props, body = connection.channel().basic_get("notifications.v1", auto_ack=True)
        assert method is not None and props.message_id == payload["id"] and json.loads(body) == payload


def test_real_broker_enforces_producer_permissions(broker):
    parameters = pika.URLParameters(broker.url())
    with pika.BlockingConnection(parameters) as connection:
        channel = connection.channel()
        channel.confirm_delivery()
        with pytest.raises(pika.exceptions.ChannelClosedByBroker):
            channel.basic_publish("identity.lifecycle.v1", "task.assigned.v1", b"{}", mandatory=True)
    with pika.BlockingConnection(pika.URLParameters(broker.url())) as connection:
        with pytest.raises(pika.exceptions.ChannelClosedByBroker):
            connection.channel().basic_get("notifications.v1")


def test_domain_http_lifecycle_events_publish_after_delay_without_consumers(database, postgres, registered_actor, broker):
    owner, member = registered_actor("Owner"), registered_actor("Member")
    app = create_app(postgres.settings(), database_factory=lambda _: database, log_stream=StringIO())
    with TestClient(app) as client:
        response = client.post("/api/v1/workspaces", json={"name": "Delayed delivery"}, headers={**owner.headers, "Idempotency-Key": str(uuid4())})
        assert response.status_code == 201
        root = "/api/v1/workspaces/" + response.json()["id"]
        invite = client.post(root + "/invitations", json={"email": f"{member.id}@example.com"}, headers={**owner.headers, "Idempotency-Key": str(uuid4())})
        assert invite.status_code == 201
        invitation_id = invite.json()["id"]
        assert client.post("/api/v1/invitations/" + invitation_id + "/response", json={"action": "accept"}, headers=member.headers).status_code == 200
        assert client.delete(root + "/members/" + str(member.id), headers=owner.headers).status_code == 204
        assert client.get(root, headers=member.headers).status_code == 404
        etag = client.get(root, headers=owner.headers).headers["etag"]
        assert client.delete(root, headers={**owner.headers, "If-Match": etag}).status_code == 204
        assert client.get(root, headers=owner.headers).status_code == 404
        assert client.get("/health/ready").status_code == 200
    with database.transaction() as session:
        rows = session.execute(select(outbox)).mappings().all()
        assert len(rows) == 3 and all(row["published_at"] is None for row in rows)
        for row in rows:
            event_contract(row["payload"])
            assert "@example.com" not in json.dumps(row["payload"])
    worker = PublisherWorker(OutboxStore(database), RabbitPublisher(broker.settings(postgres.settings())), stream=StringIO())
    assert sum(worker.tick() for _ in range(4)) == 3
    received = []
    with broker.probe() as connection:
        channel = connection.channel()
        for queue, count in (("notifications.v1", 1), ("task-cleanup.v1", 2)):
            for _ in range(count):
                method, _, body = channel.basic_get(queue, auto_ack=True)
                assert method is not None
                received.append(json.loads(body))
    assert {value["type"] for value in received} == {row["event_type"] for row in rows}
    assert {value["id"] for value in received} == {str(row["event_id"]) for row in rows}
