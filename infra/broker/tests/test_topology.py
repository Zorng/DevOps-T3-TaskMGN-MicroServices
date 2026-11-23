"""Offline checks of the broker configuration files. No broker needed.

These keep definitions.json, the permission table and the event contract in step:
if api_contracts/events/README.md changes a route, this suite fails until the
broker configuration is updated.
"""
import json
import pathlib
import re

import provision  # scripts/provision.py

BROKER_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = BROKER_DIR.parent.parent
DEFS = json.loads((BROKER_DIR / "definitions.json").read_text())
QUEUES = {q["name"]: q for q in DEFS["queues"]}
LADDER_MS = {"5s": 5_000, "30s": 30_000, "2m": 120_000, "10m": 600_000, "30m": 1_800_000}
PRODUCER_USER = {"Identity & Workspace": "identity", "Task": "task"}


def contract_routes():
    """event type -> (producer, queue), read from the contract's event table."""
    text = (REPO_ROOT / "api_contracts" / "events" / "README.md").read_text()
    pattern = re.compile(r"^\|\s*`?([a-z_.]+\.v1)`?\s*\|\s*([^|]+?)\s*\|\s*`?([a-z\-]+\.v1)`?\s*\|", re.M)
    return {m.group(1): (m.group(2), m.group(3)) for m in pattern.finditer(text)}


def bindings(source):
    return {(b["routing_key"], b["destination"]) for b in DEFS["bindings"] if b["source"] == source}


def test_contract_has_five_routes_to_two_queues():
    routes = contract_routes()
    assert len(routes) == 5
    assert {queue for _, queue in routes.values()} == {"notifications.v1", "task-cleanup.v1"}


def test_every_contract_route_is_bound_and_nothing_extra():
    expected = {(key, queue) for key, (_, queue) in contract_routes().items()}
    assert bindings("events") == expected


def test_each_event_can_only_be_published_by_its_contract_producer():
    for key, (producer, _) in contract_routes().items():
        owner = PRODUCER_USER[producer]
        for name, role in provision.ROLES.items():
            topic = role["topic"]
            allowed = bool(topic and re.match(topic["write"], key))
            assert allowed == (name == owner), f"{name} / {key}"


def test_services_cannot_declare_or_delete_queues_and_exchanges():
    for name, role in provision.ROLES.items():
        assert role["configure"] == "^$", name


def test_only_consumers_can_read_their_queue_and_nobody_reads_dead_letters():
    assert provision.ROLES["notification"]["read"] == r"^notifications\.v1$"
    assert provision.ROLES["task"]["read"] == r"^task-cleanup\.v1$"
    assert provision.ROLES["identity"]["read"] == "^$"
    for role in provision.ROLES.values():
        assert not re.match(role["read"], "notifications.v1.dlq")
        assert not re.match(role["read"], "task-cleanup.v1.dlq")


def test_every_queue_is_durable():
    assert all(q["durable"] and not q["auto_delete"] for q in DEFS["queues"])
    assert all(e["durable"] for e in DEFS["exchanges"])


def test_main_queues_are_quorum_with_dead_letter_store():
    for name in ("notifications.v1", "task-cleanup.v1"):
        args = QUEUES[name]["arguments"]
        assert args["x-queue-type"] == "quorum"
        assert args["x-dead-letter-exchange"] == "dead-letter"
        assert args["x-dead-letter-routing-key"] == name
        # Keep the message in the main queue until the dead-letter queue confirms it.
        assert args["x-dead-letter-strategy"] == "at-least-once"
        assert (name, f"{name}.dlq") in bindings("dead-letter")


def test_dead_letter_queues_never_discard_messages():
    for name in ("notifications.v1.dlq", "task-cleanup.v1.dlq"):
        args = QUEUES[name]["arguments"]
        assert args == {"x-queue-type": "quorum"}


def test_retry_ladder_matches_the_contract_delays_and_returns_to_main_queue():
    for main in ("notifications.v1", "task-cleanup.v1"):
        for suffix, ttl in LADDER_MS.items():
            name = f"{main}.retry.{suffix}"
            args = QUEUES[name]["arguments"]
            assert args["x-message-ttl"] == ttl
            assert args["x-dead-letter-exchange"] == "retry-return"
            assert args["x-dead-letter-routing-key"] == main
            assert (name, name) in bindings("retry")
        assert (main, main) in bindings("retry-return")


def test_no_credentials_in_the_configuration_files():
    for name in ("definitions.json", "rabbitmq.conf", "enabled_plugins"):
        text = (BROKER_DIR / name).read_text().lower()
        assert "password" not in text
