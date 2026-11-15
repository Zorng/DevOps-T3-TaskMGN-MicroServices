from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from identity_workspace.correlation import normalize_request_id
from identity_workspace.health import HealthResult, liveness_result, readiness_result


def test_liveness_has_no_dependency_requirement():
    assert liveness_result() == HealthResult(200, "ok")


def test_readiness_succeeds_for_usable_storage():
    database = Mock()
    database.check_readiness.return_value = True
    assert readiness_result(database) == HealthResult(200, "ok")
    database.check_readiness.assert_called_once_with()


@pytest.mark.parametrize("result", [False, None, 1, "true"])
def test_readiness_requires_an_explicit_success(result):
    database = Mock()
    database.check_readiness.return_value = result
    assert readiness_result(database) == HealthResult(503, "unavailable")


def test_readiness_without_started_resources_is_unavailable():
    assert readiness_result(None) == HealthResult(503, "unavailable")


def test_readiness_error_is_sanitized(caplog):
    database = Mock()
    database.check_readiness.side_effect = RuntimeError("synthetic-secret-in-driver-error")
    assert readiness_result(database) == HealthResult(503, "unavailable")
    assert "synthetic-secret-in-driver-error" not in caplog.text


def test_valid_request_id_is_preserved():
    identifier = str(uuid4())
    assert normalize_request_id(identifier) == identifier
    assert normalize_request_id(identifier.upper()) == identifier


@pytest.mark.parametrize("value", [None, "", "not-a-uuid", "123", "0" * 32, " " + str(uuid4())])
def test_invalid_request_id_gets_a_new_uuid(value):
    identifier = normalize_request_id(value)
    assert UUID(identifier).version == 4
    assert identifier != value
