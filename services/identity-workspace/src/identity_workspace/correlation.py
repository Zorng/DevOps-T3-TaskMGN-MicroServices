"""Correlation UUID normalization, reusable by the later HTTP middleware."""

from contextvars import ContextVar
from uuid import UUID, uuid4

request_id_context: ContextVar[str | None] = ContextVar("identity_request_id", default=None)


def normalize_request_id(value: str | None) -> str:
    if value:
        try:
            parsed = UUID(value)
        except ValueError:
            pass
        else:
            if str(parsed) == value.lower():
                return str(parsed)
    return str(uuid4())
