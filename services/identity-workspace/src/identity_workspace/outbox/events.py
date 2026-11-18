"""Closed event contract shared by transaction writers and the publisher."""

from importlib.resources import files
import json
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from identity_workspace.persistence import outbox

ROUTES = {
    "invitation.created.v1": "notifications.v1",
    "workspace.member_removed.v1": "task-cleanup.v1",
    "workspace.deleted.v1": "task-cleanup.v1",
}
SCHEMA = json.loads(files(__package__).joinpath("event.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class InvalidEvent(ValueError):
    """Deliberately carries no payload or schema diagnostic."""


def encode_event(payload: dict) -> bytes:
    valid = False
    try:
        valid = payload["type"] in ROUTES and VALIDATOR.is_valid(payload)
        if valid:
            kind, data = payload["type"], payload["data"]
            field, version = {
                "invitation.created.v1": ("invitation_id", 1),
                "workspace.member_removed.v1": ("membership_id", 2),
                "workspace.deleted.v1": ("workspace_id", None),
            }[kind]
            valid = payload["aggregate_id"] == data[field] and (version is None or payload["aggregate_version"] == version)
            valid = valid and UUID(payload["id"]).version == 4
        body = json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        valid = valid and len(body) <= 65536
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        valid = False
    if not valid:
        raise InvalidEvent("Invalid Identity lifecycle event; content omitted.")
    return body


def append_event(session, payload: dict, *, created_at):
    """Validate before inserting, using the caller's existing domain transaction."""
    encode_event(payload)
    session.execute(outbox.insert().values(
        event_id=UUID(payload["id"]), event_type=payload["type"], payload=payload,
        created_at=created_at, next_attempt_at=created_at,
    ))
