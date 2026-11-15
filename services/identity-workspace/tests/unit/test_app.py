from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from identity_workspace.config import ConfigurationError
from identity_workspace.main import create_app


def test_factory_validates_configuration_without_global_app():
    with pytest.raises(ConfigurationError, match="IDENTITY_DATABASE_URL"):
        create_app()


def test_factory_accepts_settings_and_defers_resource_creation(settings):
    factory = Mock()
    app = create_app(settings, database_factory=factory)
    assert app.state.settings is settings
    assert app.state.database is None
    factory.assert_not_called()


def test_lifespan_creates_and_closes_injected_resources(settings):
    database = Mock()
    factory = Mock(return_value=database)
    app = create_app(settings, database_factory=factory)
    with TestClient(app):
        assert app.state.database is database
        factory.assert_called_once_with(settings)
        database.close.assert_not_called()
    database.close.assert_called_once_with()
    assert app.state.database is None


def test_apps_have_separate_lifecycle_resources(settings):
    first, second = Mock(), Mock()
    app_one = create_app(settings, database_factory=Mock(return_value=first))
    app_two = create_app(settings, database_factory=Mock(return_value=second))
    with TestClient(app_one), TestClient(app_two):
        assert app_one.state.database is first
        assert app_two.state.database is second
    first.close.assert_called_once()
    second.close.assert_called_once()


def test_failed_resource_creation_does_not_leave_initialized_state(settings):
    app = create_app(settings, database_factory=Mock(side_effect=RuntimeError("Synthetic startup failure")))
    with pytest.raises(RuntimeError, match="Identity application startup failed"):
        with TestClient(app):
            pytest.fail("Startup must not succeed.")
    assert app.state.database is None


def test_cleanup_failure_still_clears_resource_reference(settings):
    database = Mock()
    database.close.side_effect = RuntimeError("Synthetic cleanup failure")
    app = create_app(settings, database_factory=Mock(return_value=database))
    with pytest.raises(RuntimeError, match="Identity application shutdown failed"):
        with TestClient(app):
            assert app.state.database is database
    assert app.state.database is None
    database.close.assert_called_once()


def test_authentication_startup_failure_disposes_already_created_database(settings):
    database = Mock()
    app = create_app(settings, database_factory=Mock(return_value=database), google_verifier_factory=Mock(side_effect=RuntimeError("synthetic-sensitive-provider-detail")))
    with pytest.raises(RuntimeError, match="Identity application startup failed"):
        with TestClient(app):
            pytest.fail("Startup must not succeed.")
    database.close.assert_called_once()
    assert app.state.auth is None and app.state.database is None


def test_provider_cleanup_failure_still_disposes_database(settings):
    database, verifier = Mock(), Mock()
    verifier.close.side_effect = RuntimeError("synthetic-sensitive-provider-detail")
    app = create_app(settings, database_factory=Mock(return_value=database), google_verifier_factory=Mock(return_value=verifier))
    with pytest.raises(RuntimeError, match="Identity application shutdown failed"):
        with TestClient(app):
            pass
    database.close.assert_called_once()
    assert app.state.auth is None and app.state.database is None
