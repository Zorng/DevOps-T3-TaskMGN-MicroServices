"""Validate actual projections against versioned, repository-owned schemas."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[3] / "api_contracts"
PUBLIC = json.loads((ROOT / "openapi/identity-workspace.openapi.json").read_text(encoding="utf-8"))
EVENTS = json.loads((ROOT / "events/event.schema.json").read_text(encoding="utf-8"))


def public_contract(value, name):
    schema = {"$ref": f"#/components/schemas/{name}", "components": PUBLIC["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def event_contract(value):
    Draft202012Validator(EVENTS, format_checker=FormatChecker()).validate(value)
