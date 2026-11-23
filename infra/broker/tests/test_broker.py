"""Operational tests against a running, provisioned broker (see ../README.md).

They prove the behaviours issue #30 asks for: confirmed and routable publication,
least-privilege access, restart persistence, redelivery of unacknowledged messages,
delayed retry, dead-lettering and manual replay that keeps the event ID, and
metrics. Services must follow the same client rules (see ../README.md).
"""
import time

import pika
import pytest
import requests
from pika.exceptions import AMQPConnectionError, ChannelClosedByBroker, UnroutableError

import replay_dead_letters
from conftest import (
    HOST, METRICS_PORT, connect, get_one, mgmt, parameters, publish_event,
    purge_all, restart_broker,
)

ROUTES = [
    ("invitation.created.v1", "identity", "notifications.v1"),
    ("task.assigned.v1", "task", "notifications.v1"),
    ("task.comment_mentioned.v1", "task", "notifications.v1"),
    ("workspace.member_removed.v1", "identity", "task-cleanup.v1"),
    ("workspace.deleted.v1", "identity", "task-cleanup.v1"),
]


def refused(action):
    """Run `action`; it must be refused with 403 ACCESS_REFUSED."""
    with pytest.raises(ChannelClosedByBroker) as caught:
        action()
    assert caught.value.reply_code == 403


# ---- Confirmed, routable publication ---------------------------------------

@pytest.mark.parametrize("key,producer,queue", ROUTES)
def test_publish_is_confirmed_and_routed_to_the_contract_queue(admin, key, producer, queue):
    connection = connect(producer)
    publish_event(connection.channel(), key, message_id=f"id-{key}")  # raises if unconfirmed/unroutable
    connection.close()

    method, props, body = get_one(admin, queue)
    assert method is not None
    assert props.message_id == f"id-{key}"
    assert props.delivery_mode == 2
    admin.basic_ack(method.delivery_tag)


def test_unroutable_publication_is_detected(admin):
    channel = admin.connection.channel()
    channel.confirm_delivery()
    with pytest.raises(UnroutableError):
        channel.basic_publish("events", "no.such.event.v1", b"{}", mandatory=True)


# ---- Least privilege ---------------------------------------------------------

def test_wrong_credentials_are_rejected():
    for user, password in (("identity", "wrong"), ("guest", "guest")):
        with pytest.raises(AMQPConnectionError):
            pika.BlockingConnection(parameters(user, password))


def test_producer_cannot_publish_another_services_event_types():
    connection = connect("identity")
    refused(lambda: publish_event(connection.channel(), "task.assigned.v1"))
    connection = connect("task")
    refused(lambda: publish_event(connection.channel(), "workspace.deleted.v1"))


def test_consumer_cannot_publish_events():
    connection = connect("notification")
    refused(lambda: publish_event(connection.channel(), "invitation.created.v1"))


def test_services_can_only_read_their_own_queue():
    refused(lambda: connect("identity").channel().basic_get("notifications.v1"))
    refused(lambda: connect("task").channel().basic_get("notifications.v1"))
    refused(lambda: connect("notification").channel().basic_get("task-cleanup.v1"))
    for user, queue in (("notification", "notifications.v1"), ("task", "task-cleanup.v1")):
        connection = connect(user)
        connection.channel().basic_get(queue)  # allowed
        connection.close()


def test_services_cannot_read_dead_letter_or_retry_queues():
    refused(lambda: connect("notification").channel().basic_get("notifications.v1.dlq"))
    refused(lambda: connect("notification").channel().basic_get("notifications.v1.retry.5s"))


def test_services_cannot_declare_or_delete_queues():
    refused(lambda: connect("notification").channel().queue_declare("rogue-queue"))
    refused(lambda: connect("task").channel().queue_delete("task-cleanup.v1"))


# ---- Durability and recovery -------------------------------------------------

def test_messages_and_routes_survive_a_broker_restart():
    purge_all()  # no `admin` fixture: its connection would not survive the restart
    connection = connect("identity")
    publish_event(connection.channel(), "invitation.created.v1", b'{"id":"survives"}', "survives")
    connection.close()

    restart_broker()

    fresh = connect("admin")
    channel = fresh.channel()
    method, props, body = get_one(channel, "notifications.v1")
    assert method is not None, "message was lost in the restart"
    assert props.message_id == "survives"
    assert body == b'{"id":"survives"}'
    channel.basic_ack(method.delivery_tag)
    fresh.close()

    queue = mgmt("/api/queues/taskmgn/notifications.v1")
    assert queue["durable"] is True
    routes = {b["routing_key"] for b in mgmt("/api/exchanges/taskmgn/events/bindings/source")}
    assert len(routes) == 5
    purge_all()


def test_unacknowledged_message_is_redelivered(admin):
    producer = connect("identity")
    publish_event(producer.channel(), "invitation.created.v1", message_id="redeliver-me")
    producer.close()

    first = connect("notification")
    method, props, _ = get_one(first.channel(), "notifications.v1")
    assert method is not None and method.redelivered is False
    first.close()  # consumer dies without acknowledging

    second = connect("notification")
    channel = second.channel()
    method, props, _ = get_one(channel, "notifications.v1")
    assert method is not None
    assert method.redelivered is True
    assert props.message_id == "redeliver-me"
    channel.basic_ack(method.delivery_tag)
    second.close()


# ---- Delayed retry and dead letters -----------------------------------------

def test_failed_message_comes_back_after_the_retry_delay_not_immediately(admin):
    producer = connect("identity")
    publish_event(producer.channel(), "invitation.created.v1", b'{"id":"retry"}', "retry-1")
    producer.close()

    consumer = connect("notification")
    channel = consumer.channel()
    channel.confirm_delivery()
    method, props, body = get_one(channel, "notifications.v1")
    # Consumer protocol: publish to the retry exchange (confirmed), then ack.
    retry_props = pika.BasicProperties(
        delivery_mode=2, content_type=props.content_type, message_id=props.message_id,
        type=props.type, headers={"x-attempt": 2},
    )
    started = time.time()
    channel.basic_publish("retry", "notifications.v1.retry.5s", body, retry_props, mandatory=True)
    channel.basic_ack(method.delivery_tag)

    method_now, _, _ = channel.basic_get("notifications.v1")
    assert method_now is None, "retry must not hot-loop back immediately"

    method, props, body = get_one(channel, "notifications.v1", wait=20)
    elapsed = time.time() - started
    assert method is not None, "message never returned from the retry queue"
    assert elapsed >= 4.5, f"came back after only {elapsed:.1f}s"
    assert props.message_id == "retry-1"
    assert body == b'{"id":"retry"}'
    assert props.headers["x-attempt"] == 2
    channel.basic_ack(method.delivery_tag)
    consumer.close()


def test_rejected_message_goes_to_the_dead_letter_queue(admin):
    producer = connect("identity")
    publish_event(producer.channel(), "invitation.created.v1", b"not-json", "malformed-1")
    producer.close()

    consumer = connect("notification")
    channel = consumer.channel()
    method, _, _ = get_one(channel, "notifications.v1")
    channel.basic_reject(method.delivery_tag, requeue=False)  # malformed: no retry
    consumer.close()

    method, props, body = get_one(admin, "notifications.v1.dlq", wait=15)
    assert method is not None
    assert props.message_id == "malformed-1"
    assert body == b"not-json"
    admin.basic_ack(method.delivery_tag)


def test_consumer_can_dead_letter_after_the_last_attempt(admin):
    consumer = connect("task")
    channel = consumer.channel()
    channel.confirm_delivery()
    props = pika.BasicProperties(delivery_mode=2, message_id="exhausted-1")
    channel.basic_publish("dead-letter", "task-cleanup.v1", b"{}", props, mandatory=True)
    consumer.close()

    method, props, _ = get_one(admin, "task-cleanup.v1.dlq")
    assert method is not None and props.message_id == "exhausted-1"
    admin.basic_ack(method.delivery_tag)


def test_manual_replay_returns_dead_letters_with_the_same_event_id(admin):
    producer = connect("identity")
    publish_event(producer.channel(), "workspace.deleted.v1", b'{"id":"replay-me"}', "replay-me")
    producer.close()
    consumer = connect("task")
    channel = consumer.channel()
    method, _, _ = get_one(channel, "task-cleanup.v1")
    channel.basic_reject(method.delivery_tag, requeue=False)
    consumer.close()
    method, _, _ = get_one(admin, "task-cleanup.v1.dlq", wait=15)
    assert method is not None
    admin.basic_nack(method.delivery_tag, requeue=True)  # leave it for the tool

    assert replay_dead_letters.replay("task-cleanup.v1", dry_run=True) == 0  # dry run moves nothing
    assert replay_dead_letters.replay("task-cleanup.v1") == 1

    method, props, body = get_one(admin, "task-cleanup.v1")
    assert method is not None
    assert props.message_id == "replay-me"
    assert body == b'{"id":"replay-me"}'
    assert "x-death" not in (props.headers or {})
    admin.basic_ack(method.delivery_tag)
    assert admin.basic_get("task-cleanup.v1.dlq")[0] is None


# ---- Health and metrics ------------------------------------------------------

def test_health_checks_report_ok():
    assert mgmt("/api/health/checks/alarms")["status"] == "ok"
    assert mgmt("/api/health/checks/local-alarms")["status"] == "ok"


def test_queue_depth_metrics_are_exposed_per_queue(admin):
    producer = connect("identity")
    publish_event(producer.channel(), "invitation.created.v1", message_id="metric-1")
    producer.close()
    time.sleep(7)  # metrics are sampled every few seconds
    text = requests.get(f"http://{HOST}:{METRICS_PORT}/metrics", timeout=10).text
    assert "rabbitmq_queue_messages_ready" in text
    lines = [l for l in text.splitlines()
             if l.startswith("rabbitmq_queue_messages_ready{") and 'queue="notifications.v1"' in l]
    assert lines and float(lines[0].rsplit(" ", 1)[1]) >= 1
    assert 'queue="task-cleanup.v1.dlq"' in text
