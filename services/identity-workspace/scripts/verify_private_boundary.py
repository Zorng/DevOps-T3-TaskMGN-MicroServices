"""Verify a real gateway hides internal Identity routes after positive controls.

Run from a trusted deployment runner with private Identity reachability. Credentials
come from files, never command-line values. No gateway is simulated by this tool.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from uuid import UUID, uuid4


class VerificationFailure(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def origin(value, *, public, same_host_private_network=False):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or any(c.isspace() for c in value) or "\\" in value
            or (port is not None and not 1 <= port <= 65535)):
            raise ValueError()
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme == "http" and not local and (public or not same_host_private_network):
            raise ValueError()
    except ValueError:
        raise VerificationFailure("invalid_origin_or_transport") from None
    return f"{parsed.scheme}://{parsed.netloc}"


def secret_file(path, *, user_token=False):
    failed = False
    try:
        with path.open("rb") as stream:
            raw = stream.read(131)
        value = raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
        pattern = r"[A-Za-z0-9_-]{43}" if user_token else r"[A-Za-z0-9_-]{43,128}"
        if len(raw) > 130 or re.fullmatch(pattern, value) is None:
            failed = True
    except (OSError, UnicodeError):
        failed = True
    if failed:
        raise VerificationFailure("credential_file_unavailable_or_invalid") from None
    return value


def transport(ca_file=None):
    context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}),
                                        urllib.request.HTTPSHandler(context=context))
    def fetch(method, url, headers):
        request = urllib.request.Request(url, method=method, headers=headers)
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise VerificationFailure("unexpected_response_size")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = None
            return response.status, body
    return fetch


def verify(private_origin, public_origin, workspace_id, task_key, notification_key, token, *, fetch):
    report = {"status": "failed", "public_origin": public_origin, "private_origin": private_origin, "checks": []}
    def probe(target, label, method, path, expected, key=None, bearer=token, extra=None):
        headers = {"X-Request-ID": str(uuid4())}
        if key is not None:
            headers["X-Service-Key"] = key
        if bearer is not None:
            headers["Authorization"] = "Bearer " + bearer
        headers.update(extra or {})
        status, payload = fetch(method, (public_origin if target == "public" else private_origin) + path, headers)
        report["checks"].append({"target": target, "check": label, "expected": expected, "observed": status})
        if status != expected:
            raise VerificationFailure("public_boundary_check_failed" if target == "public" else "private_control_failed")
        return payload

    try:
        if private_origin == public_origin or secrets.compare_digest(task_key, notification_key):
            raise VerificationFailure("origins_and_service_credentials_must_be_distinct")
        intro_path = "/internal/v1/auth/introspect"
        access_path = f"/internal/v1/workspaces/{workspace_id}/access"
        identity = probe("private", "task_introspection", "POST", intro_path, 200, task_key)
        notification = probe("private", "notification_introspection", "POST", intro_path, 200, notification_key)
        if (not isinstance(identity, dict) or set(identity) != {"user", "session_expires_at"}
            or set(identity["user"]) != {"id", "display_name"} or notification != identity
            or not identity["session_expires_at"].endswith("Z")
            or datetime.fromisoformat(identity["session_expires_at"]) <= datetime.now(timezone.utc)):
            raise VerificationFailure("private_identity_projection_failed")
        UUID(identity["user"]["id"])
        access = probe("private", "task_workspace_access", "POST", access_path, 200, task_key)
        if (set(access) != {"actor", "workspace_id", "workspace_version", "actor_membership_id", "actor_role", "members"}
            or access["actor"] != identity["user"] or access["workspace_id"] != str(workspace_id)
            or type(access["workspace_version"]) is not int or not 1 <= access["workspace_version"] <= 9007199254740991
            or not 1 <= len(access["members"]) <= 20):
            raise VerificationFailure("private_snapshot_projection_failed")
        mine = None
        for member in access["members"]:
            if set(member) != {"membership_id", "user", "role", "joined_at"} or set(member["user"]) != {"id", "display_name"}:
                raise VerificationFailure("private_member_projection_failed")
            UUID(member["membership_id"])
            UUID(member["user"]["id"])
            if member["role"] not in {"owner", "member"}:
                raise VerificationFailure("private_member_projection_failed")
            if member["user"]["id"] == identity["user"]["id"]:
                mine = member
        if mine is None or mine["membership_id"] != access["actor_membership_id"] or mine["role"] != access["actor_role"]:
            raise VerificationFailure("private_actor_projection_failed")
        probe("private", "notification_access_denied", "POST", access_path, 403, notification_key)
        probe("private", "missing_key_denied", "POST", intro_path, 401)
        probe("private", "invalid_key_denied", "POST", intro_path, 401, secrets.token_urlsafe(32))
        probe("private", "missing_bearer_denied", "POST", intro_path, 401, task_key, bearer=None)
        me = probe("public", "public_identity_positive_control", "GET", "/api/v1/me", 200, task_key)
        if me["user"]["id"] != identity["user"]["id"]:
            raise VerificationFailure("public_identity_control_failed")
        probe("public", "assertion_headers_cannot_replace_bearer", "GET", "/api/v1/me", 401,
              task_key, bearer=None, extra={"X-User-ID": identity["user"]["id"], "X-User-Role": "owner"})
        for label, path in (
            ("introspection", intro_path), ("workspace_access", access_path),
            ("encoded_prefix", "/%69nternal/v1/auth/introspect"),
            ("encoded_separator", "/internal%2fv1/auth/introspect"),
            ("duplicate_separator", "//internal/v1/auth/introspect"),
            ("dot_segment", "/api/v1/../internal/v1/auth/introspect"),
        ):
            probe("public", label + "_not_routed", "POST", path, 404, task_key)
        for path in ("/health/live", "/health/ready", "/metrics"):
            probe("public", "operational_route_not_routed", "GET", path, 404, task_key)
        for method in ("GET", "OPTIONS"):
            probe("public", method.lower() + "_internal_not_routed", method, intro_path, 404, task_key)
        report["status"] = "passed"
    except VerificationFailure as error:
        report["reason"] = str(error)
    except Exception:
        # Never include network exception strings, response bodies, headers or tokens.
        report["reason"] = "transport_or_response_verification_failed"
    report["verified_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--private-base-url", required=True)
    parser.add_argument("--workspace-id", required=True, type=UUID)
    parser.add_argument("--task-key-file", required=True, type=Path)
    parser.add_argument("--notification-key-file", required=True, type=Path)
    parser.add_argument("--user-token-file", required=True, type=Path)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--same-host-private-network", action="store_true",
                        help="Allow HTTP on a same-host private container network; not for cross-host traffic.")
    parser.add_argument("--report", type=Path, default=Path(__file__).resolve().parents[1] / "test-results/private-boundary.json")
    args = parser.parse_args()
    protected_paths = [args.task_key_file, args.notification_key_file, args.user_token_file]
    if args.ca_file is not None:
        protected_paths.append(args.ca_file)
    if args.report.resolve() in {path.resolve() for path in protected_paths}:
        print("Refusing to overwrite a credential or CA file with a verification report.")
        return 1
    try:
        public = origin(args.public_base_url, public=True)
        private = origin(args.private_base_url, public=False, same_host_private_network=args.same_host_private_network)
        report = verify(private, public, args.workspace_id, secret_file(args.task_key_file),
                        secret_file(args.notification_key_file), secret_file(args.user_token_file, user_token=True),
                        fetch=transport(args.ca_file))
    except Exception:
        report = {"status": "failed", "reason": "configuration_or_credential_setup_failed", "checks": []}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Private-boundary verification " + report["status"] + ". Report excludes credentials and response bodies.")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
