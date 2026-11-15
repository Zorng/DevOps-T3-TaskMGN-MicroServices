"""Strict JSON models and reusable contract text/identifier/date types."""

from datetime import date
import re
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", hide_input_in_errors=True, allow_inf_nan=False,
    )


def trim_text(value):
    return value.strip() if isinstance(value, str) else value


def require_nonblank(value):
    if isinstance(value, str) and not value.strip():
        raise ValueError("Must contain non-whitespace text.")
    return value


def parse_uuid(value):
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            identifier = UUID(value)
        except ValueError:
            pass
        else:
            if str(identifier) == value.lower():
                return identifier
    raise ValueError("Must be a canonical UUID string.")


def parse_calendar_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("Must be a valid YYYY-MM-DD calendar date.")


# Python len(str) and Pydantic's string bounds count Unicode code points.
WorkspaceName = Annotated[str, BeforeValidator(trim_text), Field(min_length=1, max_length=100)]
InvitationEmailText = Annotated[str, BeforeValidator(trim_text), Field(min_length=1, max_length=254)]
MemberSearchText = Annotated[str, Field(max_length=100)]
UUIDValue = Annotated[UUID, BeforeValidator(parse_uuid)]
CalendarDate = Annotated[date, BeforeValidator(parse_calendar_date)]
