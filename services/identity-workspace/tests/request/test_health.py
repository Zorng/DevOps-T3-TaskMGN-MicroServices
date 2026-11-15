from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from identity_workspace.main import create_app

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[4] / "api_contracts/openapi/identity-workspace.openapi.json").read_text(encoding="utf-8")
)


def assert_health_contract(response, path, status_code, status):
    operation = CONTRACT["paths"][path]["get"]
    assert str(status_code) in operation["responses"]
    assert response.status_code == status_code
    assert response.json() == {"status": status}
    assert response.json()["status"] in CONTRACT["components"]["schemas"]["Health"]["properties"]["status"]["enum"]
    assert response.headers["cache-control"] == "no-store"
    assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
    assert response.headers["content-type"] == "application/json"


@pytest.mark.parametrize("storage_available", [True, False])
def test_live_returns_contract_response_without_calling_database(settings, storage_available):
    database = Mock()
    database.check_readiness.return_value = storage_available
    app = create_app(settings, database_factory=Mock(return_value=database))
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert_health_contract(response, "/health/live", 200, "ok")
    database.check_readiness.assert_not_called()


@pytest.mark.parametrize("storage_available", [True, False])
def test_ready_returns_contract_response_for_database_status(settings, storage_available):
    database = Mock()
    database.check_readiness.return_value = storage_available
    app = create_app(settings, database_factory=Mock(return_value=database))
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert_health_contract(response, "/health/ready", 200 if storage_available else 503, "ok" if storage_available else "unavailable")
    database.check_readiness.assert_called_once_with()


def test_ready_does_not_disclose_dependency_error(settings, caplog):
    database = Mock()
    database.check_readiness.side_effect = RuntimeError("synthetic-secret-database-password")
    app = create_app(settings, database_factory=Mock(return_value=database))
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert_health_contract(response, "/health/ready", 503, "unavailable")
    assert "synthetic-secret-database-password" not in response.text + caplog.text


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
@pytest.mark.parametrize("identifier", [str(uuid4()), None, "invalid"])
def test_health_request_ids(settings, path, identifier):
    database = Mock()
    database.check_readiness.return_value = True
    app = create_app(settings, database_factory=Mock(return_value=database))
    with TestClient(app) as client:
        response = client.get(path, headers={"X-Request-ID": identifier} if identifier else {})
    assert_health_contract(response, path, 200, "ok")
    if identifier and identifier != "invalid":
        assert response.headers["x-request-id"] == identifier


def test_health_without_lifespan_reports_unavailable_storage(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.database = None
        assert_health_contract(client.get("/health/ready"), "/health/ready", 503, "unavailable")
        assert_health_contract(client.get("/health/live"), "/health/live", 200, "ok")


def test_liveness_remains_responsive_while_readiness_waits(settings):
    entered, release = Event(), Event()

    def slow_check():
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("Synthetic stalled dependency.")
        return True

    database = Mock()
    database.check_readiness.side_effect = slow_check
    app = create_app(settings, database_factory=Mock(return_value=database))
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.get, "/health/ready")
        try:
            assert entered.wait(timeout=2)
            assert_health_contract(client.get("/health/live"), "/health/live", 200, "ok")
            assert not pending.done()
        finally:
            release.set()
        assert_health_contract(pending.result(timeout=2), "/health/ready", 200, "ok")
