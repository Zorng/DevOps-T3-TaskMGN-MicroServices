from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("private_boundary_verifier", ROOT / "scripts/verify_private_boundary.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def fake_transport(d, *, exposed=None, private_status=None, public_identity_status=200, redirect=False):
    identity = deepcopy(d.store.introspect.return_value)
    identity["session_expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    snapshot = deepcopy(d.store.workspace_access.return_value)
    def fetch(method, url, headers):
        if url.startswith("https://private.example"):
            if private_status is not None: return private_status, {}
            if "Authorization" not in headers or "X-Service-Key" not in headers: return 401, {}
            key = headers["X-Service-Key"]
            if key not in {d.task_key, d.notification_key}: return 401, {}
            if url.endswith("/access"):
                return (403, {}) if key == d.notification_key else (200, snapshot)
            return 200, identity
        if url.endswith("/api/v1/me"):
            return (401, {}) if "Authorization" not in headers else (public_identity_status, {"user": identity["user"]})
        if redirect: return 302, {}
        return (exposed or 404), {}
    return fetch


def run(d, **changes):
    return verifier.verify("https://private.example", "https://public.example", UUID(d.workspace_id),
                           d.task_key, d.notification_key, "S" * 43, fetch=fake_transport(d, **changes))


def test_real_verification_requires_successful_private_and_public_controls(private_dependencies):
    result = run(private_dependencies)
    assert result["status"] == "passed" and len(result["checks"]) >= 15
    assert any(c["check"] == "public_identity_positive_control" for c in result["checks"])


@pytest.mark.parametrize("status", [200, 401, 403, 405, 500])
def test_authentication_failure_cannot_mask_publicly_routed_internal_endpoint(private_dependencies, status):
    result = run(private_dependencies, exposed=status)
    assert result["status"] == "failed" and result["reason"] == "public_boundary_check_failed"


@pytest.mark.parametrize("status", [401, 403, 404, 503])
def test_an_unreachable_or_unconfigured_private_service_cannot_pass(private_dependencies, status):
    assert run(private_dependencies, private_status=status)["status"] == "failed"


def test_an_arbitrary_public_404_website_does_not_prove_gateway_routing(private_dependencies):
    assert run(private_dependencies, public_identity_status=404)["status"] == "failed"


def test_redirects_are_not_accepted_as_a_boundary(private_dependencies):
    assert run(private_dependencies, redirect=True)["status"] == "failed"
    assert verifier.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example") is None


def test_exception_and_report_never_include_credentials(private_dependencies):
    d = private_dependencies
    def fail(*args): raise RuntimeError(d.task_key + d.notification_key + "S" * 43)
    result = verifier.verify("https://private.example", "https://public.example", UUID(d.workspace_id),
                             d.task_key, d.notification_key, "S" * 43, fetch=fail)
    serialized = json.dumps(result)
    assert result["status"] == "failed" and all(key not in serialized for key in (d.task_key, d.notification_key, "S" * 43))


@pytest.mark.parametrize("url", ["http://public.example", "https://user:password@example.com", "https://example.com/path", "https://example.com?token=private", "https://example.com#secret"])
def test_public_probes_require_https_origin_without_embedded_credentials(url):
    with pytest.raises(verifier.VerificationFailure): verifier.origin(url, public=True)


def test_local_and_explicit_same_host_private_transport_are_supported():
    assert verifier.origin("http://127.0.0.1:8000", public=True) == "http://127.0.0.1:8000"
    with pytest.raises(verifier.VerificationFailure): verifier.origin("http://identity-workspace:8000", public=False)
    assert verifier.origin("http://identity-workspace:8000", public=False, same_host_private_network=True).startswith("http:")


def test_report_cannot_overwrite_a_credential_file(tmp_path, private_dependencies, monkeypatch):
    d = private_dependencies
    task, notification, user = tmp_path / "task", tmp_path / "notification", tmp_path / "user"
    task.write_text(d.task_key); notification.write_text(d.notification_key); user.write_text("S" * 43)
    monkeypatch.setattr("sys.argv", ["verify_private_boundary.py", "--public-base-url", "https://public.example",
        "--private-base-url", "https://private.example", "--workspace-id", d.workspace_id,
        "--task-key-file", str(task), "--notification-key-file", str(notification),
        "--user-token-file", str(user), "--report", str(task)])
    assert verifier.main() == 1 and task.read_text() == d.task_key
