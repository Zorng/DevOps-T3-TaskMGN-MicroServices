"""Generate ignored local test-broker secrets/topology; no credentials on stdout."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets


def password_hash(password):
    salt = secrets.token_bytes(4)
    return base64.b64encode(salt + hashlib.sha256(salt + password.encode()).digest()).decode()


def setup(directory: Path, *, host="broker", port=5672):
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    outputs = [directory / name for name in ("broker-definitions.json", "broker-url", "broker-probe-url")]
    if any(path.exists() for path in outputs):
        if not all(path.is_file() for path in outputs):
            raise ValueError("Partial broker setup; restore the missing files before retrying.")
        return  # Never rotate credentials behind an existing broker database.
    producer, probe = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    queues = ("notifications.v1", "task-cleanup.v1")
    definition = {
        "users": [{"name": name, "password_hash": password_hash(password),
                   "hashing_algorithm": "rabbit_password_hashing_sha256", "tags": []}
                  for name, password in (("identity-producer", producer), ("delivery-probe", probe))],
        "vhosts": [{"name": "identity"}],
        "permissions": [
            {"user": "identity-producer", "vhost": "identity", "configure": "^$", "write": "^identity[.]lifecycle[.]v1$", "read": "^$"},
            {"user": "delivery-probe", "vhost": "identity", "configure": "^$", "write": "^$",
             "read": "^(notifications[.]v1|task-cleanup[.]v1)([.]dead)?$"},
        ],
        "topic_permissions": [{"user": "identity-producer", "vhost": "identity", "exchange": "identity.lifecycle.v1",
                               "write": "^(invitation[.]created[.]v1|workspace[.]member_removed[.]v1|workspace[.]deleted[.]v1)$", "read": "^$"}],
        "exchanges": [{"name": name, "vhost": "identity", "type": "topic" if name == "identity.lifecycle.v1" else "direct", "durable": True, "auto_delete": False, "internal": False, "arguments": {}}
                      for name in ("identity.lifecycle.v1", "identity.deadletters.v1")],
        "queues": [], "bindings": [],
    }
    for queue in queues:
        definition["queues"].extend([
            {"name": queue, "vhost": "identity", "durable": True, "auto_delete": False,
             "arguments": {"x-queue-type": "quorum", "x-dead-letter-exchange": "identity.deadletters.v1", "x-dead-letter-routing-key": queue}},
            {"name": queue + ".dead", "vhost": "identity", "durable": True, "auto_delete": False, "arguments": {"x-queue-type": "quorum"}},
        ])
        definition["bindings"].append({"source": "identity.deadletters.v1", "vhost": "identity", "destination": queue + ".dead",
                                       "destination_type": "queue", "routing_key": queue, "arguments": {}})
    for kind, queue in (("invitation.created.v1", queues[0]), ("workspace.member_removed.v1", queues[1]), ("workspace.deleted.v1", queues[1])):
        definition["bindings"].append({"source": "identity.lifecycle.v1", "vhost": "identity", "destination": queue,
                                       "destination_type": "queue", "routing_key": kind, "arguments": {}})
    # Required for quorum at-least-once dead lettering; consumers still own
    # bounded processing retries and confirmed operator replay.
    definition["policies"] = [{"vhost": "identity", "name": "durable-dead-lettering", "pattern": "^(notifications[.]v1|task-cleanup[.]v1)$",
                               "apply-to": "quorum_queues", "priority": 0,
                               "definition": {"dead-letter-strategy": "at-least-once", "overflow": "reject-publish"}}]
    values = [json.dumps(definition, indent=2), f"amqp://identity-producer:{producer}@{host}:{port}/identity",
              f"amqp://delivery-probe:{probe}@{host}:{port}/identity"]
    for path, value in zip(outputs, values, strict=True):
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(value + "\n")
        if os.name != "nt":
            path.chmod(0o644)  # Single-file container mounts; directory traversal is owner-only.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets-dir", type=Path, default=Path(".secrets"))
    args = parser.parse_args()
    try:
        setup(args.secrets_dir)
    except (OSError, ValueError):
        print("Broker setup failed; check file permissions and complete existing setup. Values omitted.")
        return 1
    print("Local broker configuration ready; existing complete configuration preserved. No secrets printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
