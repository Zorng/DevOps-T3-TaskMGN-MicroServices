#!/usr/bin/env python3
"""Provision the broker: topology first, then users and permissions.

Idempotent: safe to run on every start and after every credential change.
Order matters. Routes are created and bound here BEFORE any producer user exists,
so producers cannot be enabled against an unprovisioned broker.

Needs only the Python standard library. Configuration comes from environment
variables (see ../.env.example); passwords are never printed or stored in Git.
"""
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VHOST = os.environ.get("BROKER_VHOST", "taskmgn")

# Least-privilege roles. Regexes are anchored. Services get NO configure
# permission, so they cannot declare, change or delete queues and exchanges.
# `topic` limits which routing keys a user may publish on the `events` exchange
# (the contract's producer for each event type).
ROLES = {
    "identity": {
        "password_env": "BROKER_IDENTITY_PASSWORD",
        "configure": "^$",
        "write": "^events$",
        "read": "^$",
        "topic": {
            "exchange": "events",
            "write": r"^(invitation\.created|workspace\.member_removed|workspace\.deleted)\.v1$",
            "read": "^$",
        },
    },
    "task": {
        "password_env": "BROKER_TASK_PASSWORD",
        "configure": "^$",
        # Publishes task events; consumes task-cleanup.v1; may use the retry and
        # dead-letter exchanges to hand its own failed messages back.
        "write": "^(events|retry|dead-letter)$",
        "read": r"^task-cleanup\.v1$",
        "topic": {
            "exchange": "events",
            "write": r"^task\.(assigned|comment_mentioned)\.v1$",
            "read": "^$",
        },
    },
    "notification": {
        "password_env": "BROKER_NOTIFICATION_PASSWORD",
        "configure": "^$",
        "write": "^(retry|dead-letter)$",
        "read": r"^notifications\.v1$",
        "topic": None,
    },
}

HERE = pathlib.Path(__file__).resolve().parent
DEFINITIONS = HERE.parent / "definitions.json"


class Api:
    def __init__(self, base, user, password):
        self.base = base.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def call(self, method, path, body=None, ok=()):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            if err.code in ok:
                return None
            raise SystemExit(f"{method} {path} failed: HTTP {err.code}")


def quote(value):
    return urllib.parse.quote(value, safe="")


def wait_until_ready(api, attempts=60):
    for _ in range(attempts):
        try:
            api.call("GET", "/api/overview")
            return
        except (SystemExit, urllib.error.URLError, ConnectionError, OSError):
            time.sleep(2)
    raise SystemExit("broker management API did not become ready")


def main():
    base = os.environ.get("BROKER_MGMT_URL", "http://127.0.0.1:15672")
    admin = os.environ.get("BROKER_ADMIN_USER")
    admin_password = os.environ.get("BROKER_ADMIN_PASSWORD")
    if not admin or not admin_password:
        raise SystemExit("BROKER_ADMIN_USER and BROKER_ADMIN_PASSWORD are required")
    passwords = {}
    for name, role in ROLES.items():
        value = os.environ.get(role["password_env"])
        if not value:
            raise SystemExit(f"{role['password_env']} is required")
        passwords[name] = value

    api = Api(base, admin, admin_password)
    wait_until_ready(api)

    # 1. Topology: vhost, exchanges, queues, bindings (additive and idempotent).
    api.call("POST", "/api/definitions", json.loads(DEFINITIONS.read_text()))
    print("topology applied")

    # 2. The admin needs access to the vhost it just created.
    api.call("PUT", f"/api/permissions/{quote(VHOST)}/{quote(admin)}",
             {"configure": ".*", "write": ".*", "read": ".*"})

    # 3. Service users, created only now that every route is bound.
    for name, role in ROLES.items():
        api.call("PUT", f"/api/users/{quote(name)}", {"password": passwords[name], "tags": ""})
        api.call("PUT", f"/api/permissions/{quote(VHOST)}/{quote(name)}",
                 {"configure": role["configure"], "write": role["write"], "read": role["read"]})
        api.call("DELETE", f"/api/topic-permissions/{quote(VHOST)}/{quote(name)}", ok=(404,))
        topic = role["topic"]
        if topic:
            api.call("PUT", f"/api/topic-permissions/{quote(VHOST)}/{quote(name)}",
                     {"exchange": topic["exchange"], "write": topic["write"], "read": topic["read"]})
        print(f"user {name} provisioned")

    # 4. Never leave the well-known default account around.
    api.call("DELETE", "/api/users/guest", ok=(404,))
    print("provisioning complete")


if __name__ == "__main__":
    sys.exit(main())
