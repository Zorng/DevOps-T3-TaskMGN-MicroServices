from datetime import datetime
from io import StringIO
import json
import logging
from uuid import uuid4

from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client.parser import text_string_to_metric_families
import pytest

from identity_workspace.correlation import request_id_context
from identity_workspace.observability import Observability, metrics_response
from identity_workspace.structured_logging import SafeJSONFormatter

ROUTES = {"/health/live", "/health/ready", "/metrics", "/items/{item_id}"}


def make_observability(level="INFO", stream=None):
    return Observability(level, lambda: ROUTES, stream=stream if stream is not None else StringIO())


def test_log_is_json_with_utc_timestamp_and_request_correlation():
    stream = StringIO()
    obs = make_observability(stream=stream)
    identifier = str(uuid4())
    token = request_id_context.set(identifier)
    try:
        obs.record_request(method="GET", route="/items/{item_id}", status_code=200, duration_seconds=0.025)
    finally:
        request_id_context.reset(token)
        obs.close()
    record = json.loads(stream.getvalue())
    assert record["request_id"] == identifier
    assert record["service"] == "identity-workspace"
    assert record["event"] == "http_request_completed"
    assert record["route"] == "/items/{item_id}"
    assert record["duration_ms"] == 25
    assert record["status_code"] == 200
    assert record["level"] == "INFO"
    assert record["timestamp"].endswith("Z")
    assert datetime.fromisoformat(record["timestamp"]).tzinfo is not None


def test_debug_logging_discards_credentials_and_untrusted_known_fields():
    stream = StringIO()
    obs = make_observability(level="DEBUG", stream=stream)
    secret = "synthetic-secret-password-token-email"
    obs.log.event(
        logging.DEBUG, secret,
        authorization="Bearer " + secret, service_key=secret, body={"password": secret},
        database_url="postgresql+psycopg://fixture_user:" + secret + "@localhost/fixture_db",
        request_id=secret, route="/unknown/" + secret, method=secret,
        status_code=secret, duration_ms=secret, error_category=secret,
        exc_info=RuntimeError(secret), stack_info=secret,
    )
    obs.close()
    text = stream.getvalue()
    record = json.loads(text)
    assert secret not in text
    assert record["event"] == "application_event"
    assert record["route"] == "unmatched"
    assert record["method"] == "OTHER"
    assert record["request_id"] is None
    assert "database_url" not in record
    assert "exc_info" not in record


def test_formatter_ignores_raw_messages_arguments_and_tracebacks():
    secret = "synthetic-secret-in-exception"
    record = logging.LogRecord("third-party", logging.ERROR, __file__, 1, secret + " %s", (secret,), (ValueError, ValueError(secret), None))
    record.stack_info = secret
    record.authorization = secret
    formatter = SafeJSONFormatter(lambda: ROUTES)
    text = formatter.format(record)
    assert secret not in text
    assert json.loads(text)["event"] == "application_event"


def test_application_log_level_is_per_instance_without_changing_root_logging():
    root = logging.getLogger()
    handlers_before, level_before = list(root.handlers), root.level
    stream_one, stream_two = StringIO(), StringIO()
    first = make_observability(level="INFO", stream=stream_one)
    second = make_observability(level="ERROR", stream=stream_two)
    try:
        for obs in (first, second):
            obs.log.event(logging.INFO, "application_started")
            obs.log.event(logging.ERROR, "application_shutdown_failed", error_category="shutdown_failed")
        assert len(stream_one.getvalue().splitlines()) == 2
        assert len(stream_two.getvalue().splitlines()) == 1
        assert root.handlers == handlers_before
        assert root.level == level_before
    finally:
        first.close()
        second.close()


def test_request_counters_latency_and_error_counters_update():
    obs = make_observability()
    try:
        obs.record_request(method="GET", route="/health/ready", status_code=503, duration_seconds=0.125)
        registry = obs.registry
        assert registry.get_sample_value("identity_http_requests_total", {"method": "GET", "route": "/health/ready", "status_code": "503"}) == 1
        assert registry.get_sample_value("identity_http_request_duration_seconds_count", {"method": "GET", "route": "/health/ready"}) == 1
        assert registry.get_sample_value("identity_http_request_duration_seconds_sum", {"method": "GET", "route": "/health/ready"}) == pytest.approx(0.125)
        assert registry.get_sample_value("identity_http_errors_total", {"kind": "server"}) == 1
    finally:
        obs.close()


def test_registry_is_private_to_each_app_instance():
    first, second = make_observability(), make_observability()
    try:
        first.record_request(method="GET", route="/health/live", status_code=200, duration_seconds=0)
        labels = {"method": "GET", "route": "/health/live", "status_code": "200"}
        assert first.registry.get_sample_value("identity_http_requests_total", labels) == 1
        assert second.registry.get_sample_value("identity_http_requests_total", labels) is None
    finally:
        first.close()
        second.close()


def test_unknown_paths_and_methods_do_not_create_unbounded_labels():
    obs = make_observability()
    try:
        for index in range(50):
            obs.record_request(method=f"synthetic-secret-{index}", route=f"/unknown/synthetic-secret-{index}", status_code=400, duration_seconds=0)
        text = obs.render_metrics().decode()
        assert "synthetic-secret" not in text
        samples = [sample for family in text_string_to_metric_families(text) for sample in family.samples if sample.name == "identity_http_requests_total"]
        assert len(samples) == 1
        assert samples[0].labels == {"method": "OTHER", "route": "unmatched", "status_code": "400"}
        assert samples[0].value == 50
    finally:
        obs.close()


def test_readiness_is_unknown_until_checked_and_records_failure_recovery():
    obs = make_observability()
    try:
        assert obs.registry.get_sample_value("identity_readiness_status") == -1
        assert obs.registry.get_sample_value("identity_readiness_last_check_timestamp_seconds") == 0
        obs.record_readiness(False, 0.1)
        assert obs.registry.get_sample_value("identity_readiness_status") == 0
        obs.record_readiness(True, 0.01)
        assert obs.registry.get_sample_value("identity_readiness_status") == 1
        assert obs.registry.get_sample_value("identity_readiness_last_check_timestamp_seconds") > 0
        assert obs.registry.get_sample_value("identity_readiness_checks_total", {"result": "unavailable"}) == 1
        assert obs.registry.get_sample_value("identity_readiness_checks_total", {"result": "ready"}) == 1
    finally:
        obs.close()


def test_metrics_response_has_prometheus_content_type_and_parsable_body():
    obs = make_observability()
    try:
        response = metrics_response(obs)
        assert response.status_code == 200
        assert response.headers["content-type"] == CONTENT_TYPE_LATEST
        families = list(text_string_to_metric_families(response.body.decode()))
        assert any(family.name == "identity_service_info" for family in families)
    finally:
        obs.close()


def test_log_sink_failure_is_counted_without_dumping_records(capsys):
    class BrokenStream:
        def write(self, text):
            raise OSError("synthetic-secret-sink-error")

        def flush(self):
            pass

    obs = make_observability(stream=BrokenStream())
    try:
        obs.record_request(method="GET", route="/health/live", status_code=200, duration_seconds=0)
        assert obs.registry.get_sample_value("identity_log_write_failures_total") == 1
        assert "synthetic-secret" not in capsys.readouterr().err
        assert obs.render_metrics()
    finally:
        obs.close()


def test_disconnected_request_is_an_internal_499_observation():
    stream = StringIO()
    obs = make_observability(stream=stream)
    try:
        obs.record_request(method="GET", route=None, status_code=None, duration_seconds=0)
        assert json.loads(stream.getvalue())["event"] == "http_request_disconnected"
        assert obs.registry.get_sample_value("identity_http_requests_total", {"method": "GET", "route": "unmatched", "status_code": "499"}) == 1
    finally:
        obs.close()


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf"), None])
def test_invalid_duration_observations_do_not_break_metrics(duration):
    obs = make_observability()
    try:
        obs.record_request(method="GET", route="/health/live", status_code=200, duration_seconds=duration)
        assert obs.registry.get_sample_value("identity_http_request_duration_seconds_sum", {"method": "GET", "route": "/health/live"}) == 0
    finally:
        obs.close()
