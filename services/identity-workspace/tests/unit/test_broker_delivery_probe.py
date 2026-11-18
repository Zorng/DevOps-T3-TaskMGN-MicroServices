"""Regression checks for delayed/duplicate receipts and real delivery failures."""

from collections import deque
from copy import deepcopy
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/verify_broker_delivery.py"))
verify_delivery = MODULE["verify_delivery"]
DeliveryCheckFailure = MODULE["DeliveryCheckFailure"]


@pytest.fixture
def delivery():
    expected, messages = {}, {}
    for index, (queue, kind) in enumerate((("notifications.v1", "invitation.created.v1"), ("task-cleanup.v1", "workspace.deleted.v1"))):
        identifier, request_id = f"synthetic-event-{index}", f"synthetic-request-{index}"
        body = json.dumps({"type": kind}).encode()
        expected[queue] = {identifier: (body, request_id)}
        messages[queue] = (SimpleNamespace(delivery_tag=index + 1), SimpleNamespace(
            message_id=identifier, correlation_id=request_id, delivery_mode=2,
            content_type="application/json", content_encoding="utf-8", app_id="identity-workspace", type=kind,
        ), body)
    return expected, messages


def test_delayed_receipts_are_polled_and_duplicates_keep_one_unique_event(delivery):
    expected, messages = delivery
    now = [0.0]
    queues = {queue: deque([message, message]) for queue, message in messages.items()}
    ack = Mock()

    def fetch(queue):
        if now[0] < 1 or not queues[queue]:
            return None, None, None
        return queues[queue].popleft()

    def pause(seconds):
        now[0] += seconds

    result = verify_delivery(expected, fetch, ack, timeout=2, clock=lambda: now[0], pause=pause)
    assert result == {"unique_notifications": 1, "unique_cleanup": 1, "deliveries": 4, "duplicate_deliveries": 2}
    assert ack.call_count == 4 and now[0] == 1


@pytest.mark.parametrize("damage", ["id", "queue", "body", "persistent", "request_id", "content_type", "encoding", "producer", "type"])
def test_wrong_route_unknown_event_or_corrupted_message_fails_unacknowledged(delivery, damage):
    expected, messages = deepcopy(delivery)
    method, properties, body = messages["notifications.v1"]
    if damage == "id": properties.message_id = "unexpected-private-event"
    elif damage == "queue": expected["notifications.v1"] = dict(expected["task-cleanup.v1"])
    elif damage == "body": body = b"private-corrupted-body"
    elif damage == "persistent": properties.delivery_mode = 1
    elif damage == "request_id": properties.correlation_id = "private-wrong-request"
    elif damage == "content_type": properties.content_type = "text/plain"
    elif damage == "encoding": properties.content_encoding = "invalid"
    elif damage == "producer": properties.app_id = "task"
    elif damage == "type": properties.type = "wrong.type"
    ack = Mock()
    with pytest.raises(DeliveryCheckFailure) as caught:
        verify_delivery(expected, lambda _: (method, properties, body), ack)
    ack.assert_not_called()
    assert "private" not in str(caught.value) + json.dumps(caught.value.summary)


def test_missing_event_still_fails_after_bounded_wait_with_safe_counts(delivery):
    expected, _ = delivery
    now = [0.0]
    def pause(seconds): now[0] += seconds
    with pytest.raises(DeliveryCheckFailure, match="expected_event_delivery_timeout") as caught:
        verify_delivery(expected, lambda _: (None, None, None), Mock(), timeout=1,
                        clock=lambda: now[0], pause=pause)
    assert now[0] == 1
    assert caught.value.summary == {"unique_notifications": 0, "unique_cleanup": 0, "deliveries": 0, "duplicate_deliveries": 0}


def test_duplicate_of_one_event_cannot_hide_another_missing_event(delivery):
    expected, messages = delivery
    original = expected["notifications.v1"]
    original["missing-event"] = next(iter(original.values()))
    queue = deque([messages["notifications.v1"]] * 10)
    now = [0.0]
    def fetch(name):
        return queue.popleft() if name == "notifications.v1" and queue else (None, None, None)
    def pause(seconds): now[0] += seconds
    with pytest.raises(DeliveryCheckFailure) as caught:
        verify_delivery(expected, fetch, Mock(), timeout=1, clock=lambda: now[0], pause=pause)
    assert caught.value.summary["unique_notifications"] == 1
    assert caught.value.summary["duplicate_deliveries"] == 9
