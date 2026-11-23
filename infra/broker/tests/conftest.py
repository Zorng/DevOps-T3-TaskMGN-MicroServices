"""Shared helpers for the broker tests.

Offline tests (test_topology.py) read files only. Live tests (test_broker.py) need
a provisioned broker: see ../README.md. Settings come from environment variables;
values in ../.env (if present) are used for any that are not already set.
"""
import os
import pathlib
import subprocess
import sys
import time

import pika
import pytest
import requests

TESTS_DIR = pathlib.Path(__file__).resolve().parent
BROKER_DIR = TESTS_DIR.parent
sys.path.insert(0, str(BROKER_DIR / "scripts"))


def _load_dotenv():
    env_file = BROKER_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

HOST = os.environ.get("BROKER_HOST", "127.0.0.1")
AMQP_PORT = int(os.environ.get("BROKER_AMQP_PORT", "5672"))
MGMT_PORT = int(os.environ.get("BROKER_MGMT_PORT", "15672"))
METRICS_PORT = int(os.environ.get("BROKER_METRICS_PORT", "15692"))
VHOST = os.environ.get("BROKER_VHOST", "taskmgn")

MAIN_QUEUES = ("notifications.v1", "task-cleanup.v1")
ALL_QUEUES = [
    name
    for main in MAIN_QUEUES
    for name in (main, f"{main}.dlq", *[f"{main}.retry.{s}" for s in ("5s", "30s", "2m", "10m", "30m")])
]


def password_for(user):
    if user == "admin":
        return os.environ["BROKER_ADMIN_USER"], os.environ["BROKER_ADMIN_PASSWORD"]
    return user, os.environ[f"BROKER_{user.upper()}_PASSWORD"]


def parameters(user, password=None):
    name, secret = password_for(user) if password is None else (user, password)
    return pika.ConnectionParameters(
        host=HOST, port=AMQP_PORT, virtual_host=VHOST,
        credentials=pika.PlainCredentials(name, secret),
        socket_timeout=10, blocked_connection_timeout=10,
    )


def connect(user):
    return pika.BlockingConnection(parameters(user))


def mgmt(path):
    name, secret = password_for("admin")
    response = requests.get(f"http://{HOST}:{MGMT_PORT}{path}", auth=(name, secret), timeout=10)
    response.raise_for_status()
    return response.json()


def publish_event(channel, key, body=b'{"id":"evt-1"}', message_id="evt-1"):
    """Publish the way services must: persistent, confirmed, mandatory."""
    channel.confirm_delivery()
    props = pika.BasicProperties(
        delivery_mode=2, content_type="application/json", message_id=message_id, type=key
    )
    channel.basic_publish("events", key, body, props, mandatory=True)


def get_one(channel, queue, wait=10.0, auto_ack=False):
    """Poll basic_get until a message arrives or `wait` seconds pass."""
    deadline = time.time() + wait
    while True:
        method, props, body = channel.basic_get(queue, auto_ack=auto_ack)
        if method is not None or time.time() >= deadline:
            return method, props, body
        time.sleep(0.25)


def wait_for_broker(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            connect("admin").close()
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("broker did not come back")


def restart_broker():
    """Restart the broker process/container. Override with BROKER_RESTART_CMD."""
    command = os.environ.get("BROKER_RESTART_CMD")
    if command:
        subprocess.run(command, shell=True, check=True)
    else:
        subprocess.run(
            ["docker", "compose", "-f", str(BROKER_DIR / "compose.yaml"), "restart", "broker"],
            check=True,
        )
    time.sleep(2)
    wait_for_broker()


def purge_all():
    """Empty every queue, using a fresh admin connection."""
    connection = connect("admin")
    channel = connection.channel()
    for queue in ALL_QUEUES:
        channel.queue_purge(queue)
    connection.close()


@pytest.fixture
def admin():
    purge_all()  # every live test starts from empty queues
    connection = connect("admin")
    yield connection.channel()
    try:
        connection.close()
    except Exception:
        pass
    purge_all()
