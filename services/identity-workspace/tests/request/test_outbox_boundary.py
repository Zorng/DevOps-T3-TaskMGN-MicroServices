"""The HTTP process does not connect to a broker, even when settings are broken."""

from io import StringIO
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from identity_workspace.main import create_app


@pytest.mark.parametrize("configuration", [
    {},
    {"broker_url": SecretStr("amqp://synthetic:sensitive-broker-password@unavailable.invalid/identity")},
    {"broker_url_file": Path("unavailable-broker-secret")},
])
@pytest.mark.parametrize("writable_database", [True, False])
def test_http_health_depends_on_database_not_publisher(settings, configuration, writable_database, monkeypatch):
    constructor = Mock(side_effect=AssertionError("HTTP must not construct a publisher"))
    monkeypatch.setattr("identity_workspace.outbox.broker.RabbitPublisher", constructor)
    database = Mock()
    database.check_readiness.return_value = writable_database
    stream = StringIO()
    app = create_app(settings.model_copy(update=configuration), database_factory=lambda _: database, log_stream=stream)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == (200 if writable_database else 503)
        assert client.get("/metrics").status_code == 200
    constructor.assert_not_called()
    assert "sensitive-broker-password" not in stream.getvalue()
