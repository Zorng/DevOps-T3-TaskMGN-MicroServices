import importlib.util
import json
import os
from pathlib import Path
import traceback
from unittest.mock import Mock
from uuid import UUID

from pydantic import SecretStr
import pytest
from sqlalchemy.exc import SQLAlchemyError

from identity_workspace.auth.service import token_hash
from identity_workspace.config import ConfigurationError, Settings, load_settings
from identity_workspace.errors import APIError
from identity_workspace.private_api.credentials import ServiceKeyConfigurationError, ServiceKeyRegistry
from identity_workspace.private_api.service import PrivateIdentityService
from tests.workspace_contract import PRIVATE, private_contract

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("setup_service_keys", ROOT / "scripts/setup_service_keys.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.mark.parametrize("operation,caller,status", [("introspect", "task", 200), ("introspect", "notification", 200),
                                                      ("workspace_access", "task", 200), ("workspace_access", "notification", 403)])
def test_private_service_enforces_caller_matrix_and_hashes_user_bearer(private_dependencies, operation, caller, status):
    d = private_dependencies
    service = PrivateIdentityService(d.store, ServiceKeyRegistry(d.settings))
    args = ([getattr(d, caller + "_key")], "S" * 43, *((UUID(d.workspace_id),) if operation == "workspace_access" else ()))
    if status == 403:
        with pytest.raises(APIError) as caught:
            getattr(service, operation)(*args)
        assert caught.value.status_code == 403
        d.store.workspace_access.assert_not_called()
    else:
        result = getattr(service, operation)(*args)
        private_contract(result, "SessionIdentity" if operation == "introspect" else "AccessContext")
        assert getattr(d.store, operation).call_args.args[0] == token_hash("S" * 43)


@pytest.mark.parametrize("operation", ["introspect", "workspace_access"])
@pytest.mark.parametrize("code,status", [("SESSION_INVALID", 401), ("NOT_FOUND", 404)])
def test_private_operations_preserve_fresh_authorization_errors(private_dependencies, operation, code, status):
    d = private_dependencies
    getattr(d.store, operation).side_effect = APIError(status, code=code)
    service = PrivateIdentityService(d.store, ServiceKeyRegistry(d.settings))
    with pytest.raises(APIError) as caught:
        getattr(service, operation)([d.task_key], "S" * 43, *((UUID(d.workspace_id),) if operation == "workspace_access" else ()))
    assert (caught.value.status_code, caught.value.code) == (status, code)


@pytest.mark.parametrize("operation", ["introspect", "workspace_access"])
def test_private_database_outage_is_safe_503(private_dependencies, operation):
    d = private_dependencies
    getattr(d.store, operation).side_effect = SQLAlchemyError("synthetic-private-database-credential")
    with pytest.raises(APIError) as caught:
        service = PrivateIdentityService(d.store, ServiceKeyRegistry(d.settings))
        getattr(service, operation)([d.task_key], "S" * 43, *((UUID(d.workspace_id),) if operation == "workspace_access" else ()))
    assert caught.value.status_code == 503 and "synthetic-private" not in str(caught.value)


@pytest.mark.parametrize("values", [[], ["bad"], ["X" * 43], ["T" * 43, "T" * 43], ["T" * 43 + "," + "N" * 43], ["é" * 43]])
def test_unknown_missing_duplicate_and_malformed_keys_are_rejected(private_dependencies, values):
    with pytest.raises(APIError) as caught:
        ServiceKeyRegistry(private_dependencies.settings).authorize(values, "introspect")
    assert caught.value.code == "SERVICE_CREDENTIAL_INVALID"


def test_no_default_key_grants_access(settings):
    with pytest.raises(APIError) as caught:
        ServiceKeyRegistry(settings).authorize(["T" * 43], "introspect")
    assert caught.value.code == "SERVICE_CREDENTIAL_INVALID"


def test_equal_per_service_keys_are_configuration_error(settings):
    settings = settings.model_copy(update={"task_service_key": SecretStr("T" * 43), "notification_service_key": SecretStr("T" * 43)})
    with pytest.raises(ServiceKeyConfigurationError) as caught:
        ServiceKeyRegistry(settings)
    assert "T" * 43 not in str(caught.value)


@pytest.mark.parametrize("name", ["TASK", "NOTIFICATION"])
@pytest.mark.parametrize("value", ["too-short", " " + "T" * 43, "T" * 129, "é" * 43])
def test_invalid_environment_keys_never_leak_from_settings(monkeypatch, name, value, settings):
    monkeypatch.setenv("IDENTITY_DATABASE_URL", settings.database_url.get_secret_value())
    monkeypatch.setenv(f"IDENTITY_{name}_SERVICE_KEY", value)
    with pytest.raises(ConfigurationError) as caught:
        load_settings(env_file=None)
    assert value not in "".join(traceback.format_exception(caught.value))


def test_optional_blank_key_configuration_preserves_public_only_setup(settings):
    copy = Settings(_env_file=None, database_url=settings.database_url, task_service_key="", notification_service_key="",
                    task_service_key_file="", notification_service_key_file="")
    assert copy.task_service_key is copy.task_service_key_file is None


def test_secret_files_and_rotation_map_distinct_callers(tmp_path, settings):
    task, notification = tmp_path / "task", tmp_path / "notification"
    task.write_text("T" * 43 + "\n"); notification.write_text("N" * 43)
    configured = settings.model_copy(update={"task_service_key_file": task, "notification_service_key_file": notification})
    old = ServiceKeyRegistry(configured)
    assert old.authorize(["T" * 43], "workspace_access") == "task"
    assert old.authorize(["N" * 43], "introspect") == "notification"
    task.write_text("R" * 43)
    new = ServiceKeyRegistry(configured)
    with pytest.raises(APIError): new.authorize(["T" * 43], "introspect")
    assert new.authorize(["R" * 43], "workspace_access") == "task"
    assert new.authorize(["N" * 43], "introspect") == "notification"


@pytest.mark.parametrize("content", [None, b"", b"private-invalid-key", b"T" * 129, b"T" * 43 + b"\n\n", b"T" * 43 + b"\x80"])
def test_bad_or_unreadable_key_file_has_no_secret_in_exception(tmp_path, settings, content):
    path = tmp_path / "key"
    if content is not None: path.write_bytes(content)
    with pytest.raises(ServiceKeyConfigurationError) as caught:
        ServiceKeyRegistry(settings.model_copy(update={"task_service_key_file": path}))
    assert "private-invalid" not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None


def test_env_and_file_sources_cannot_be_mixed(tmp_path, private_dependencies):
    d = private_dependencies
    path = tmp_path / "key"; path.write_text("T" * 43)
    with pytest.raises(ServiceKeyConfigurationError):
        ServiceKeyRegistry(d.settings.model_copy(update={"task_service_key_file": path}))


def test_setup_preserves_distinct_random_keys_without_printing_them(tmp_path, capsys):
    setup.main(tmp_path)
    paths = [tmp_path / ".secrets" / name for name in ("task-service-key", "notification-service-key")]
    first = [path.read_text() for path in paths]
    setup.main(tmp_path)
    assert first == [path.read_text() for path in paths]
    assert len(first[0]) == 43 and first[0] != first[1]
    output = capsys.readouterr().out
    assert all(key not in output for key in first)
    if os.name != "nt":
        assert (tmp_path / ".secrets").stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o644 for path in paths)


def test_consumer_fixture_cases_match_internal_contract(private_dependencies):
    for case in private_dependencies.fixtures["cases"]:
        assert str(case["status"]) in PRIVATE["paths"][case["path_template"]]["post"]["responses"]
        schema = "Error" if case["status"] != 200 else ("SessionIdentity" if case["path_template"].endswith("introspect") else "AccessContext")
        private_contract(case["response"], schema)


def test_setup_rejects_malformed_existing_key_without_printing_its_bytes(tmp_path):
    directory = tmp_path / ".secrets"; directory.mkdir()
    (directory / "task-service-key").write_bytes(b"private-canary-" + b"\x80" * 43)
    with pytest.raises(ValueError) as caught:
        setup.main(tmp_path)
    assert "private-canary" not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None
