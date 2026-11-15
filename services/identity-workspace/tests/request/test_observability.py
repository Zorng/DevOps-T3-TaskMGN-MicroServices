from io import StringIO
import json
from unittest.mock import Mock
import traceback
from uuid import uuid4

from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client.parser import text_string_to_metric_families
import pytest

from identity_workspace.main import create_app


@pytest.fixture
def instrumented_app(settings):
    stream, database = StringIO(), Mock()
    database.check_readiness.return_value = True
    app = create_app(settings, database_factory=Mock(return_value=database), log_stream=stream)

    @app.get("/api/v1/_tests/items/{item_id}")
    def item(item_id: str):
        return {"ok": True}

    @app.get("/api/v1/_tests/failure/{detail}")
    def fail(detail: str):
        raise RuntimeError(detail)

    return app, stream, database


def records(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_metrics_is_private_operational_text_with_shared_headers(instrumented_app):
    app, _, database = instrumented_app
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert list(text_string_to_metric_families(response.text))
    database.check_readiness.assert_not_called()


def test_request_metrics_and_log_id_match_the_response(instrumented_app):
    app, stream, _ = instrumented_app
    identifier = str(uuid4())
    with TestClient(app) as client:
        response = client.get("/health/live", headers={"X-Request-ID": identifier})
        metrics = client.get("/metrics")
    event = next(item for item in records(stream) if item["event"] == "http_request_completed" and item.get("route") == "/health/live")
    assert event["request_id"] == response.headers["x-request-id"] == identifier
    assert event["method"] == "GET" and event["status_code"] == 200
    assert event["duration_ms"] >= 0
    assert 'identity_http_requests_total{method="GET",route="/health/live",status_code="200"} 1.0' in metrics.text
    assert identifier not in metrics.text


def test_metrics_is_available_during_dependency_failure_and_records_recovery(instrumented_app):
    app, stream, database = instrumented_app
    database.check_readiness.side_effect = RuntimeError("synthetic-secret-database-password")
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 503
        failed = client.get("/metrics")
        assert failed.status_code == 200
        assert "identity_readiness_status 0.0" in failed.text
        database.check_readiness.assert_called_once()
        assert "synthetic-secret" not in stream.getvalue() + failed.text
        database.check_readiness.side_effect = None
        assert client.get("/health/ready").status_code == 200
        assert "identity_readiness_status 1.0" in client.get("/metrics").text
    failures = [item for item in records(stream) if item["event"] == "readiness_checked" and item["readiness"] == "unavailable"]
    assert len(failures) == 1
    assert failures[0]["error_category"] == "storage_unavailable"
    assert failures[0]["request_id"]


def test_metrics_before_first_probe_reports_unknown(instrumented_app):
    app, _, _ = instrumented_app
    with TestClient(app) as client:
        response = client.get("/metrics")
        assert "identity_readiness_status -1.0" in response.text
        assert "identity_readiness_last_check_timestamp_seconds 0.0" in response.text


def test_debug_logs_and_metric_labels_never_include_request_credentials(settings):
    stream = StringIO()
    app = create_app(settings.model_copy(update={"log_level": "DEBUG"}), database_factory=Mock(return_value=Mock()), log_stream=stream)
    secret = "synthetic-secret-private-value"
    email = "private-fixture@example.test"

    @app.get("/api/v1/_tests/items/{item_id}")
    def item(item_id: str):
        return {}

    @app.get("/api/v1/_tests/failure/{detail}")
    def failure(detail: str):
        raise RuntimeError(detail)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + secret, "X-Service-Key": secret, "Cookie": "session=" + secret}
        assert client.get("/api/v1/_tests/items/" + secret, headers=headers).status_code == 200
        assert client.get("/health/live?email=" + email, headers=headers).status_code == 400
        assert client.request("GET", "/health/live", content=json.dumps({"id_token": secret}), headers=headers).status_code == 400
        assert client.get("/api/v1/_tests/failure/" + secret).status_code == 500
        assert client.get("/unknown/" + secret).status_code == 404
        output = client.get("/metrics").text
    logs = stream.getvalue()
    assert secret not in logs + output
    assert email not in logs + output
    assert "/api/v1/_tests/items/{item_id}" in logs + output
    failures = [item for item in records(stream) if item.get("status_code") == 500]
    assert failures[0]["error_category"] == "internal_error"
    assert failures[0]["level"] == "ERROR"


def test_error_log_level_does_not_disable_metrics(settings):
    stream = StringIO()
    database = Mock()
    database.check_readiness.return_value = False
    app = create_app(settings.model_copy(update={"log_level": "ERROR"}), database_factory=Mock(return_value=database), log_stream=stream)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.get("/metrics").status_code == 200
    events = records(stream)
    assert len(events) == 1
    assert events[0]["status_code"] == 503
    assert events[0]["level"] == "ERROR"
    assert app.state.observability.registry.get_sample_value("identity_http_requests_total", {"method": "GET", "route": "/health/live", "status_code": "200"}) == 1


@pytest.mark.parametrize("method,path,status", [("POST", "/metrics", 405), ("GET", "/metrics?unknown=example", 400)])
def test_metrics_rejects_unsupported_methods_and_queries(instrumented_app, method, path, status):
    app, _, _ = instrumented_app
    with TestClient(app) as client:
        response = client.request(method, path)
    assert response.status_code == status
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"


def test_unexpected_http_method_is_bounded_in_logs_and_labels(instrumented_app):
    app, stream, _ = instrumented_app
    with TestClient(app) as client:
        assert client.request("SYNTHETICSECRET", "/health/live").status_code == 405
        output = client.get("/metrics").text
    assert "SYNTHETICSECRET" not in stream.getvalue() + output
    assert any(item.get("method") == "OTHER" for item in records(stream))


def test_body_limit_rejection_is_observed_without_logging_payload(instrumented_app):
    app, stream, _ = instrumented_app
    with TestClient(app) as client:
        assert client.request("GET", "/health/live", content=b"x" * 65537).status_code == 413
    event = next(item for item in records(stream) if item.get("status_code") == 413)
    assert event["error_category"] == "payload_too_large"
    assert event["route"] == "unmatched"
    assert "x" * 100 not in stream.getvalue()


@pytest.mark.parametrize("stage", ["startup", "shutdown"])
def test_lifecycle_logs_and_server_tracebacks_never_expose_driver_messages(settings, stage):
    stream = StringIO()
    secret = "synthetic-secret-in-lifecycle-error"
    database = Mock()
    factory = Mock(return_value=database)
    if stage == "startup":
        factory.side_effect = RuntimeError(secret)
    else:
        database.close.side_effect = RuntimeError(secret)
    app = create_app(settings, database_factory=factory, log_stream=stream)
    with pytest.raises(RuntimeError, match=f"Identity application {stage} failed") as caught:
        with TestClient(app):
            pass
    assert secret not in stream.getvalue()
    assert secret not in "".join(traceback.format_exception(caught.value))
    assert any(item["event"] == f"application_{stage}_failed" for item in records(stream))
    assert app.state.database is None
