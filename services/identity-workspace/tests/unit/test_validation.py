from datetime import date
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
import pytest

from identity_workspace.validation import CalendarDate, ContractModel, InvitationEmailText, UUIDValue, WorkspaceName, require_nonblank


class ExampleInput(ContractModel):
    name: WorkspaceName
    count: int
    enabled: bool
    identifier: UUIDValue
    day: CalendarDate


def example(**overrides):
    values = {"name": "  Workspace  ", "count": 1, "enabled": True, "identifier": str(uuid4()), "day": "2026-11-15"}
    values.update(overrides)
    return ExampleInput.model_validate(values)


def test_strict_json_model_normalizes_semantic_string_fields():
    model = example()
    assert model.name == "Workspace"
    assert model.identifier.version == 4
    assert model.day == date(2026, 11, 15)


@pytest.mark.parametrize("override", [{"count": "1"}, {"count": True}, {"enabled": "true"}, {"enabled": 1}, {"name": 42}, {"unknown": "value"}, {"name": " \t "}])
def test_json_types_and_unknown_properties_are_strict(override):
    with pytest.raises(ValidationError):
        example(**override)


def test_text_limits_count_unicode_code_points_after_trimming():
    assert example(name="  " + "😀" * 100 + " ").name == "😀" * 100
    with pytest.raises(ValidationError):
        example(name="😀" * 101)
    # Combining marks are separate code points, not one visual character.
    assert len(example(name="e\u0301" * 50).name) == 100
    with pytest.raises(ValidationError):
        example(name="e\u0301" * 51)


def test_invitation_email_text_preserves_case_and_aliases():
    adapter = TypeAdapter(InvitationEmailText)
    assert adapter.validate_python("  Name.With+Alias@example.test  ") == "Name.With+Alias@example.test"
    with pytest.raises(ValidationError):
        adapter.validate_python("x" * 255)


@pytest.mark.parametrize("value", ["2026-02-29", "2026-13-01", "20261115", "2026-11-15T00:00:00Z", 123])
def test_calendar_dates_require_real_iso_dates(value):
    with pytest.raises(ValidationError):
        example(day=value)


def test_valid_leap_date_is_accepted():
    assert example(day="2028-02-29").day == date(2028, 2, 29)


@pytest.mark.parametrize("value", [123, "invalid", uuid4().hex])
def test_uuid_fields_require_canonical_format(value):
    with pytest.raises(ValidationError):
        example(identifier=value)


def test_nonblank_validation_preserves_comment_offsets():
    assert require_nonblank("  @Example hello  ") == "  @Example hello  "
    with pytest.raises(ValueError):
        require_nonblank(" \t\n")
