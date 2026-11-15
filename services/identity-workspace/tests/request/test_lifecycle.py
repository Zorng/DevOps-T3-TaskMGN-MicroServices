from unittest.mock import Mock

import psycopg
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from identity_workspace.main import create_app


def test_request_can_use_injected_application_resources(settings):
    database = Mock()
    app = create_app(settings, database_factory=Mock(return_value=database))

    # This route exists only in the test's app; it is not a service API.
    @app.get("/_test/resources")
    def inspect_resources(request: Request):
        return {"available": request.app.state.database is database}

    with TestClient(app) as client:
        response = client.get("/_test/resources")
        assert response.status_code == 200
        assert response.json() == {"available": True}
    database.close.assert_called_once()


def test_app_starts_and_serves_without_connecting_to_postgres(monkeypatch, settings):
    connect = Mock(side_effect=psycopg.OperationalError("Synthetic database outage."))
    monkeypatch.setattr(psycopg, "connect", connect)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/not-an-implemented-route")
        assert response.status_code == 404
        connect.assert_not_called()
    assert app.state.database is None
    connect.assert_not_called()


def test_request_failure_does_not_prevent_shutdown_cleanup(settings):
    database = Mock()
    app = create_app(settings, database_factory=Mock(return_value=database))

    @app.get("/_test/failure")
    def fail_request():
        raise RuntimeError("Synthetic request failure")

    with TestClient(app) as client:
        response = client.get("/_test/failure")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "Synthetic request failure" not in response.text
    database.close.assert_called_once()
    assert app.state.database is None


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_generated_documentation_is_not_exposed_by_default(settings, path):
    app = create_app(settings, database_factory=Mock(return_value=Mock()))
    with TestClient(app) as client:
        assert client.get(path).status_code == 404
