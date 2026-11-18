"""Contract, transport, retry and secret boundaries without a real broker."""

from copy import deepcopy
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import runpy
import traceback
from unittest.mock import Mock
from uuid import UUID, uuid4

import pika
from pydantic import SecretStr
import pytest

from identity_workspace.config import ConfigurationError
from identity_workspace.outbox.broker import PublishFailure, RabbitPublisher, broker_url
from identity_workspace.outbox.events import InvalidEvent, SCHEMA, encode_event
from identity_workspace.outbox.store import Delivery, OutboxStore
from identity_workspace.outbox.worker import PublisherWorker, retry_delay

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def payload():
    event = deepcopy(json.loads((ROOT / "api_contracts/examples/events.json").read_text(encoding="utf-8"))[0])
    event["id"] = str(uuid4())
    return event


def test_bundled_schema_matches_canonical_contract():
    assert SCHEMA == json.loads((ROOT / "api_contracts/events/event.schema.json").read_text(encoding="utf-8"))


def test_consumer_handoff_fixtures_preserve_duplicate_ids_and_contract():
    fixture = json.loads((ROOT / "services/identity-workspace/fixtures/identity-lifecycle-events.json").read_text(encoding="utf-8"))
    assert set(fixture["events"]) == {"invitation.created.v1", "workspace.member_removed.v1", "workspace.deleted.v1"}
    for value in fixture["events"].values():
        encode_event(value)
    for scenario in fixture["scenarios"]:
        values = [fixture["events"][key] for key in scenario["event_sequence"]]
        assert len({value["id"] for value in values}) < len(values)
        for value in values:
            assert encode_event(value) == encode_event(fixture["events"][value["type"]])
    removal = fixture["events"]["workspace.member_removed.v1"]
    rejoin = next(s for s in fixture["scenarios"] if s["name"] == "old-removal-after-rejoin")
    assert rejoin["new_membership_id"] != removal["data"]["membership_id"]


def test_encoding_is_stable_and_closed(payload):
    assert encode_event(payload) == encode_event(dict(reversed(list(payload.items()))))
    assert json.loads(encode_event(payload)) == payload
    assert len(encode_event(payload)) <= 65536


@pytest.mark.parametrize("mutation", [
    lambda e: e.update(email="sensitive@example.com"),
    lambda e: e["data"].update(token="sensitive-token"),
    lambda e: e.update(type="task.assigned.v1"),
    lambda e: e.update(producer="task"),
    lambda e: e.update(id="not-a-uuid"),
    lambda e: e.update(id="00000000-0000-0000-0000-000000000000"),
    lambda e: e.update(request_id="bad"),
    lambda e: e.update(aggregate_id=str(uuid4())),
    lambda e: e.update(aggregate_version=2),
    lambda e: e.update(aggregate_version=9007199254740992),
    lambda e: e.update(occurred_at="yesterday"),
    lambda e: e["data"]["actor"].update(display_name="sensitive" * 10000),
])
def test_invalid_events_have_content_free_errors(payload, mutation):
    mutation(payload)
    with pytest.raises(InvalidEvent) as caught:
        encode_event(payload)
    assert "sensitive" not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None


@pytest.mark.parametrize("attempt", [1, 2, 3, 6, 7, 1000000000])
def test_retry_grows_with_jitter_and_is_capped(attempt):
    low, high = retry_delay(attempt, lambda: 0), retry_delay(attempt, lambda: 1)
    assert 1 <= low <= high <= 60
    assert low == min(60, 2 ** min(attempt - 1, 6))


@pytest.fixture
def transport(settings):
    settings = settings.model_copy(update={"broker_url": SecretStr("amqp://producer:sensitive-password@localhost/identity")})
    connection, channel = Mock(), Mock()
    connection.channel.return_value = channel
    factory = Mock(return_value=connection)
    return RabbitPublisher(settings, connection_factory=factory), factory, connection, channel


def test_transport_confirms_persistent_mandatory_publication(transport, payload):
    publisher, factory, connection, channel = transport
    body = encode_event(payload)
    publisher.publish(payload, body)
    calls = [c[0] for c in channel.method_calls]
    assert calls == ["confirm_delivery", "basic_publish"]
    params = channel.basic_publish.call_args.kwargs
    assert params["mandatory"] is True
    assert params["body"] == body and params["routing_key"] == payload["type"]
    props = params["properties"]
    assert (props.delivery_mode, props.message_id, props.correlation_id, props.content_type) == (
        2, payload["id"], payload["request_id"], "application/json")
    assert factory.call_args.args[0].connection_attempts == 1
    assert factory.call_args.args[0].blocked_connection_timeout == 5
    connection.close.assert_called_once()


@pytest.mark.parametrize("failure,reason", [
    (pika.exceptions.UnroutableError([]), "unroutable"),
    (pika.exceptions.NackError([]), "nack"),
    (TimeoutError("sensitive-password"), "broker_unavailable"),
    (RuntimeError("sensitive-password"), "broker_unavailable"),
])
def test_transport_failure_and_close_diagnostics_are_sanitized(transport, payload, failure, reason):
    publisher, _, connection, channel = transport
    channel.basic_publish.side_effect = failure
    connection.close.side_effect = RuntimeError("sensitive-password")
    with pytest.raises(PublishFailure, match=reason) as caught:
        publisher.publish(payload, encode_event(payload))
    assert "sensitive-password" not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None


def test_transport_connection_failure_does_not_publish(transport, payload):
    publisher, factory, _, channel = transport
    factory.side_effect = RuntimeError("sensitive-password")
    with pytest.raises(PublishFailure, match="broker_unavailable"):
        publisher.publish(payload, encode_event(payload))
    channel.basic_publish.assert_not_called()


def test_confirm_timeout_interrupts_wait(transport, payload):
    publisher, _, connection, channel = transport
    channel.basic_publish.side_effect = lambda **_: connection.call_later.call_args_list[0].args[1]()
    with pytest.raises(PublishFailure, match="broker_unavailable"):
        publisher.publish(payload, encode_event(payload))


def test_confirmed_publish_is_success_even_if_close_fails(transport, payload):
    publisher, _, connection, _ = transport
    connection.close.side_effect = RuntimeError("sensitive-password")
    publisher.publish(payload, encode_event(payload))


def test_tls_context_requires_valid_certificate_and_hostname(settings, payload):
    import ssl
    configured = settings.model_copy(update={"environment": "production", "broker_url": SecretStr("amqps://producer:synthetic@broker/identity")})
    connection = Mock()
    factory = Mock(return_value=connection)
    RabbitPublisher(configured, connection_factory=factory).publish(payload, encode_event(payload))
    options = factory.call_args.args[0].ssl_options
    assert options.context.verify_mode == ssl.CERT_REQUIRED
    assert options.context.check_hostname and options.server_hostname == "broker"


@pytest.mark.parametrize("url", [None, "", "amqp://producer:sensitive@localhost/identity?heartbeat=0", "https://producer:sensitive@localhost/", "amqp://localhost/", "amqp://producer:sensitive@localhost:99999/", "amqp://producer:sensitive@localhost/\n"])
def test_worker_rejects_invalid_broker_configuration_safely(settings, url):
    settings = settings.model_copy(update={"broker_url": SecretStr(url) if url else None})
    with pytest.raises(ConfigurationError) as caught:
        broker_url(settings)
    assert "sensitive" not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None


def test_broker_secret_file_and_production_tls(settings, tmp_path):
    secret = tmp_path / "broker-url"
    secret.write_text("amqps://producer:sensitive@broker/identity\n", encoding="utf-8")
    configured = settings.model_copy(update={"environment": "production", "broker_url_file": secret})
    assert broker_url(configured) == "amqps://producer:sensitive@broker/identity"
    with pytest.raises(ConfigurationError):
        broker_url(configured.model_copy(update={"broker_url": SecretStr("amqps://p:s@broker/identity")}))
    secret.write_text("amqp://producer:sensitive@broker/identity", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        broker_url(configured)
    secret.write_bytes(b"sensitive" * 1000)
    with pytest.raises(ConfigurationError):
        broker_url(configured)


@pytest.fixture
def worker(payload):
    store, publisher, stream = Mock(spec=OutboxStore), Mock(), StringIO()
    store.claim.return_value = Delivery(UUID(payload["id"]), 1, payload)
    store.complete.return_value = True
    return PublisherWorker(store, publisher, stream=stream, jitter=lambda: 0), store, publisher, stream


def test_worker_marks_only_after_publish_and_logs_only_safe_metadata(worker, payload):
    worker, store, publisher, stream = worker
    chronology = Mock()
    chronology.attach_mock(publisher, "publisher")
    chronology.attach_mock(store, "store")
    assert worker.tick()
    assert [c[0] for c in chronology.method_calls] == ["store.claim", "publisher.publish", "store.complete"]
    record = json.loads(stream.getvalue())
    assert record["event_id"] == payload["id"] and record["request_id"] == payload["request_id"]
    assert "data" not in record and payload["data"]["actor"]["display_name"] not in stream.getvalue()
    assert not worker.last_failed


def test_worker_failure_is_pending_with_backoff_and_original_id(worker):
    worker, store, publisher, _ = worker
    publisher.publish.side_effect = PublishFailure("unroutable")
    assert worker.tick() and worker.last_failed
    store.complete.assert_called_once_with(store.claim.return_value, delay=1)
    assert worker.failures.labels(reason="unroutable")._value.get() == 1
    assert worker.published._value.get() == 0


def test_worker_invalid_record_never_reaches_broker(worker):
    worker, store, publisher, _ = worker
    store.claim.return_value.payload["data"]["token"] = "sensitive-token"
    worker.tick()
    publisher.publish.assert_not_called()
    assert store.complete.call_args.kwargs == {"delay": 1}


def test_worker_restart_keeps_id_and_fences_stale_completion(worker):
    worker, store, publisher, _ = worker
    store.claim.return_value = replace(store.claim.return_value, attempt=2)
    store.complete.return_value = False
    worker.tick()
    assert publisher.publish.call_args.args[0]["id"] == str(store.claim.return_value.event_id)
    assert worker.retries._value.get() == 1 and worker.published._value.get() == 0


def test_worker_empty_and_backlog_observation(worker):
    worker, store, publisher, stream = worker
    store.claim.return_value = None
    assert worker.tick() is False
    publisher.publish.assert_not_called()
    store.stats.return_value = (3, 301)
    worker.observe()
    assert worker.pending._value.get() == 3 and worker.age._value.get() == 301
    assert json.loads(stream.getvalue())["event"] == "outbox_backlog_old"


@pytest.mark.parametrize("days,limit", [(6, 1), (7, 0), (7, 10001), (True, 1)])
def test_purge_rejects_unsafe_retention_without_database_access(days, limit):
    database = Mock()
    with pytest.raises(ValueError):
        OutboxStore(database).purge_confirmed(days=days, limit=limit)
    database.transaction.assert_not_called()


def test_broker_setup_is_secret_safe_and_preserves_existing_keys(tmp_path):
    setup = runpy.run_path(str(ROOT / "services/identity-workspace/scripts/setup_broker.py"))["setup"]
    directory = tmp_path / "secrets"
    setup(directory)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    setup(directory)
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    definition = json.loads(before["broker-definitions.json"])
    assert all(queue["durable"] and queue["arguments"]["x-queue-type"] == "quorum" for queue in definition["queues"])
    assert {b["routing_key"] for b in definition["bindings"] if b["source"] == "identity.lifecycle.v1"} == {
        "invitation.created.v1", "workspace.member_removed.v1", "workspace.deleted.v1"}
    assert b"identity-producer:" not in before["broker-definitions.json"]
    assert all("password" not in user for user in definition["users"])
    (directory / "broker-url").unlink()
    with pytest.raises(ValueError, match="Partial"):
        setup(directory)


@pytest.mark.parametrize("mode,expected", [("empty", 0), ("success", 0), ("publish_failure", 1), ("storage_failure", 1)])
def test_once_command_returns_failure_without_loop_or_secret_output(settings, payload, monkeypatch, capsys, mode, expected):
    from sqlalchemy.exc import OperationalError
    from identity_workspace.outbox import worker as module
    store, publisher = Mock(spec=OutboxStore), Mock()
    store.claim.return_value = None if mode == "empty" else Delivery(UUID(payload["id"]), 1, payload)
    store.complete.return_value = True
    store.stats.return_value = (0, 0)
    if mode == "publish_failure":
        publisher.publish.side_effect = PublishFailure("broker_unavailable")
    if mode == "storage_failure":
        store.claim.side_effect = OperationalError("sensitive-password", None, None)
    monkeypatch.setattr("sys.argv", ["worker", "--once"])
    monkeypatch.setattr(module, "load_settings", lambda: settings)
    monkeypatch.setattr(module, "create_database", Mock())
    monkeypatch.setattr(module, "OutboxStore", Mock(return_value=store))
    monkeypatch.setattr(module, "RabbitPublisher", Mock(return_value=publisher))
    monkeypatch.setattr(module.signal, "signal", Mock())
    server = Mock()
    monkeypatch.setattr(module, "start_http_server", server)
    assert module.main() == expected
    server.assert_not_called()
    output = capsys.readouterr()
    assert "sensitive-password" not in output.out + output.err


def test_purge_command_requires_no_broker_configuration(settings, monkeypatch):
    from identity_workspace.outbox import worker as module
    store = Mock(spec=OutboxStore)
    store.purge_confirmed.return_value = 3
    monkeypatch.setattr("sys.argv", ["worker", "--purge-confirmed"])
    monkeypatch.setattr(module, "load_settings", lambda: settings)
    monkeypatch.setattr(module, "create_database", Mock())
    monkeypatch.setattr(module, "OutboxStore", Mock(return_value=store))
    publisher = Mock()
    monkeypatch.setattr(module, "RabbitPublisher", publisher)
    assert module.main() == 0
    publisher.assert_not_called()
    store.purge_confirmed.assert_called_once_with()
