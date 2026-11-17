"""Build and verify the real service image using an isolated Compose project."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.request
from uuid import UUID, uuid4

from setup_local_db import main as setup_database

SERVICE_ROOT = Path(__file__).resolve().parents[1]


class SmokeFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


class Docker:
    def __init__(self, environment, compose_args):
        self.environment = environment
        self.compose_args = compose_args
        self.secrets = ()

    def run(self, *args, timeout=180):
        result = subprocess.run(
            ["docker", *args], cwd=SERVICE_ROOT, env=self.environment,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
        if result.returncode:
            detail = result.stderr[-2000:]
            for secret in self.secrets:
                detail = detail.replace(secret, "[redacted]")
            raise SmokeFailure(f"Docker {args[0]} failed (exit {result.returncode}): {detail}")
        return result.stdout.strip()

    def compose(self, *args):
        return self.run(*self.compose_args, *args)


def request(base_url, path, expected_status, *, method="GET", body=None, headers=None):
    request_id = str(uuid4())
    request_headers = {**(headers or {}), "X-Request-ID": request_id}
    data = None
    if body is not None:
        request_headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    operation = urllib.request.Request(base_url + path, headers=request_headers, method=method, data=data)
    try:
        response = urllib.request.urlopen(operation, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        require(response.status == expected_status, f"Unexpected HTTP status for a {expected_status} probe.")
        require(response.headers.get("Cache-Control") == "no-store", "Missing no-store header.")
        require(response.headers.get("X-Request-ID") == request_id, "Request ID did not propagate.")
        require(str(UUID(response.headers["X-Request-ID"])) == request_id, "Invalid request ID.")
        return response.headers, response.read().decode("utf-8")


def health(base_url, readiness):
    for path, status, value in [
        ("/health/live", 200, "ok"),
        ("/health/ready", readiness, "ok" if readiness == 200 else "unavailable"),
    ]:
        headers, body = request(base_url, path, status)
        require(headers.get_content_type() == "application/json", "Health content type differs.")
        require(json.loads(body) == {"status": value}, "Health response shape differs.")
    headers, metrics = request(base_url, "/metrics", 200)
    require(headers.get_content_type() == "text/plain", "Metrics content type differs.")
    expected = "1.0" if readiness == 200 else "0.0"
    require(f"identity_readiness_status {expected}\n" in metrics, "Readiness metric differs.")
    require("identity_http_requests_total{" in metrics, "Request metrics are missing.")
    return metrics


def registered_recipient_fixture(docker):
    """Seed only a registered user/session; joins use the public invitation API."""
    result = json.loads(docker.compose("exec", "--no-TTY", "identity", "python", "-c", "\n".join([
        "import json, secrets",
        "from datetime import timedelta",
        "from uuid import uuid4",
        "from identity_workspace.config import load_settings",
        "from identity_workspace.database import create_database",
        "from identity_workspace.auth.service import token_hash",
        "from identity_workspace.auth.store import database_now",
        "from identity_workspace.persistence import users, sessions",
        "db = create_database(load_settings())",
        "identifier, token = uuid4(), secrets.token_urlsafe(32)",
        "with db.transaction() as s:",
        " now = database_now(s)",
        " s.execute(users.insert().values(id=identifier, provider='google', provider_subject=str(identifier), display_name='Smoke Member', email='private-member@example.com', created_at=now))",
        " s.execute(sessions.insert().values(id=uuid4(), user_id=identifier, token_hash=token_hash(token), created_at=now, expires_at=now + timedelta(hours=8)))",
        "db.close()",
        "print(json.dumps({'token': token, 'user_id': str(identifier)}))",
    ])))
    docker.secrets = (*docker.secrets, result["token"], "private-member@example.com", "PRIVATE-MEMBER@EXAMPLE.COM")
    return result


def smoke(image, skip_build, report):
    report.parent.mkdir(parents=True, exist_ok=True)
    results = {"status": "running", "image": image, "checks": []}

    def record(message):
        results["checks"].append(message)
        print(f"PASS: {message}", flush=True)

    report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    tools = SERVICE_ROOT / ".tools"
    tools.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="identity-container-smoke-", dir=tools) as name:
            directory = Path(name).resolve()
            require(directory.is_relative_to(tools.resolve()), "Invalid temporary workspace.")
            (directory / ".env.example").write_bytes((SERVICE_ROOT / ".env.example").read_bytes())
            setup_database(directory)
            config = directory / ".env.container"
            config.write_text(
                config.read_text(encoding="utf-8").replace("IDENTITY_LOG_LEVEL=INFO", "IDENTITY_LOG_LEVEL=DEBUG"),
                encoding="utf-8",
            )
            # A container smoke run must never use live Google configuration.
            lines = [line for line in config.read_text(encoding="utf-8").splitlines() if not line.startswith("IDENTITY_GOOGLE_CLIENT_ID=")]
            config.write_text("\n".join([*lines, "IDENTITY_GOOGLE_CLIENT_ID=", ""]), encoding="utf-8")
            compose_env = directory / "compose.env"
            compose_env.write_text("", encoding="utf-8")
            environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("IDENTITY_")}
            environment.update(
                IDENTITY_IMAGE=image, IDENTITY_DB_PORT="0", IDENTITY_HTTP_PORT="0",
                IDENTITY_CONTAINER_ENV_FILE=str(config),
                IDENTITY_DB_SECRETS_DIR=str(directory / ".secrets"),
                COMPOSE_PROFILES="",
            )
            docker = Docker(environment, [
                "compose", "--file", str(SERVICE_ROOT / "compose.yaml"),
                "--project-name", f"identity-smoke-{uuid4().hex[:12]}",
                "--env-file", str(compose_env), "--profile", "app",
            ])
            docker.secrets = tuple(
                (directory / ".secrets" / file).read_text(encoding="utf-8").strip()
                for file in ["database-owner-password", "database-app-password"]
            )
            docker.compose("config", "--quiet")
            if not skip_build:
                print("Building the locked service image...", flush=True)
                docker.run("build", "--tag", image, ".", timeout=600)
                record("Service image built successfully")
            image_info = json.loads(docker.run("image", "inspect", image))[0]
            require(image_info["Config"]["User"] == "10001:10001", "Image must select the non-root user.")
            require(not any(secret in json.dumps(image_info) for secret in docker.secrets), "Image metadata contains a credential.")
            results.update(image_id=image_info["Id"], platform=f"{image_info['Os']}/{image_info['Architecture']}")
            try:
                print("Starting disposable PostgreSQL, migration, and application containers...", flush=True)
                docker.compose("up", "--detach", "--no-build", "--wait", "--wait-timeout", "90", "identity")
                migrate_id = docker.compose("ps", "--all", "--quiet", "migrate")
                require(docker.run("inspect", "--format", "{{.State.ExitCode}}", migrate_id) == "0", "Migration did not succeed.")
                docker.compose("run", "--rm", "--no-deps", "migrate")
                record("Fresh migrations and repeated upgrade succeeded")

                identity_id = docker.compose("ps", "--quiet", "identity")
                require(docker.run("inspect", "--format", "{{.HostConfig.ReadonlyRootfs}}", identity_id) == "true", "Container root filesystem must be read-only.")
                runtime = json.loads(docker.compose("exec", "--no-TTY", "identity", "python", "-c", "\n".join([
                    "import importlib.metadata as m, json, os, pathlib, shutil, sys",
                    "installed = {d.metadata['Name'].lower() for d in m.distributions()}",
                    "private = ['.env', '.env.container', '.secrets', '.tools', '.uv-cache', 'tests']",
                    "print(json.dumps({'uid': os.geteuid(), 'gid': os.getegid(), 'python': sys.version.split()[0],",
                    " 'development_packages': sorted(installed & {'pytest', 'httpx2', 'jsonschema', 'uv', 'uv-build'}),",
                    " 'private_paths': [p for p in private if (pathlib.Path('/app') / p).exists()],",
                    " 'uv_binary': shutil.which('uv')}))",
                ])))
                require(runtime["uid"] == runtime["gid"] == 10001, "Application must run as the non-root user.")
                require(runtime["python"] == (SERVICE_ROOT / ".python-version").read_text().strip(), "Container Python version differs.")
                require(not runtime["development_packages"] and not runtime["private_paths"] and runtime["uv_binary"] is None, "Runtime includes development tools or private paths.")
                results["runtime"] = runtime
                record("Non-root runtime, read-only filesystem, selected Python, and runtime-only dependencies verified")

                port = int(docker.compose("port", "identity", "8000").rsplit(":", 1)[1])
                base_url = f"http://127.0.0.1:{port}"
                health(base_url, 200)
                record("Live/ready return 200; headers, JSON shapes, and metrics are correct without a broker")
                google_probe = "synthetic-google-probe-" + uuid4().hex
                for path, method, expected, payload, code in [
                    ("/api/v1/auth/google/challenges", "POST", 503, None, "DEPENDENCY_UNAVAILABLE"),
                    ("/api/v1/auth/google/exchange", "POST", 503, {"challenge_id": str(uuid4()), "id_token": google_probe}, "DEPENDENCY_UNAVAILABLE"),
                    ("/api/v1/auth/logout", "POST", 401, None, "AUTH_REQUIRED"),
                    ("/api/v1/me", "GET", 401, None, "AUTH_REQUIRED"),
                ]:
                    _, payload_text = request(base_url, path, expected, method=method, body=payload, headers={"Origin": "http://localhost:5173"})
                    require(json.loads(payload_text)["error"]["code"] == code, "Packaged auth route returned the wrong error.")
                record("Packaged authentication routes enforce Google configuration and bearer requirements")
                # Seed a synthetic session only in this disposable smoke database.
                # No production bypass or live provider credentials are enabled.
                actor = json.loads(docker.compose("exec", "--no-TTY", "identity", "python", "-c", "\n".join([
                    "import json, secrets",
                    "from datetime import timedelta",
                    "from uuid import uuid4",
                    "from identity_workspace.config import load_settings",
                    "from identity_workspace.database import create_database",
                    "from identity_workspace.auth.service import token_hash",
                    "from identity_workspace.auth.store import database_now",
                    "from identity_workspace.persistence import users, sessions",
                    "db = create_database(load_settings())",
                    "identifier, token = uuid4(), secrets.token_urlsafe(32)",
                    "with db.transaction() as s:",
                    " now = database_now(s)",
                    " s.execute(users.insert().values(id=identifier, provider='google', provider_subject=str(identifier), display_name='Smoke Fixture', email='smoke@example.com', created_at=now))",
                    " s.execute(sessions.insert().values(id=uuid4(), user_id=identifier, token_hash=token_hash(token), created_at=now, expires_at=now + timedelta(hours=8)))",
                    "db.close()",
                    "print(json.dumps({'token': token, 'user_id': str(identifier)}))",
                ])))
                docker.secrets = (*docker.secrets, actor["token"])
                bearer = {"Authorization": "Bearer " + actor["token"]}
                create_headers = {**bearer, "Idempotency-Key": str(uuid4())}
                _, denied = request(base_url, "/api/v1/workspaces", 401)
                require(json.loads(denied)["error"]["code"] == "AUTH_REQUIRED", "Workspace auth guard differs.")
                original_headers, original = request(base_url, "/api/v1/workspaces", 201, method="POST", body={"name": " Smoke Team "}, headers=create_headers)
                workspace = json.loads(original)
                require(workspace["name"] == "Smoke Team" and workspace["my_role"] == "owner" and workspace["member_count"] == 1, "Workspace create projection differs.")
                require(original_headers["ETag"] == '"1"', "Initial ETag differs.")
                replay_headers, replay = request(base_url, "/api/v1/workspaces", 201, method="POST", body={"name": "Smoke Team"}, headers=create_headers)
                require(json.loads(replay) == workspace and replay_headers["ETag"] == original_headers["ETag"], "Create retry changed the original response.")
                target = "/api/v1/workspaces/" + workspace["id"]
                _, listing = request(base_url, "/api/v1/workspaces?limit=1", 200, headers=bearer)
                require(json.loads(listing) == {"items": [workspace], "next_cursor": None}, "Workspace list differs.")
                _, read_back = request(base_url, target, 200, headers=bearer)
                require(json.loads(read_back) == workspace, "Workspace read differs.")
                _, missing = request(base_url, target, 428, method="PATCH", body={"name": "Renamed"}, headers=bearer)
                require(json.loads(missing)["error"]["code"] == "PRECONDITION_REQUIRED", "Missing precondition differs.")
                renamed_headers, renamed = request(base_url, target, 200, method="PATCH", body={"name": "Renamed"}, headers={**bearer, "If-Match": '"1"'})
                require(json.loads(renamed)["version"] == 2 and renamed_headers["ETag"] == '"2"', "Rename version differs.")
                member = registered_recipient_fixture(docker)
                member_bearer = {"Authorization": "Bearer " + member["token"]}
                invitation_ids = []

                def invite_member():
                    invitation_headers = {**bearer, "Idempotency-Key": str(uuid4())}
                    _, pending_text = request(base_url, target + "/invitations", 201, method="POST",
                                              body={"email": " PRIVATE-MEMBER@EXAMPLE.COM "}, headers=invitation_headers)
                    pending = json.loads(pending_text)
                    _, retry_text = request(base_url, target + "/invitations", 201, method="POST",
                                            body={"email": "private-member@example.com"}, headers=invitation_headers)
                    require(json.loads(retry_text) == pending, "Invitation retry changed its original response.")
                    invitation_ids.append(pending["id"])
                    return "/api/v1/invitations/" + pending["id"]

                invitation_target = invite_member()
                request(base_url, invitation_target, 200, headers=bearer)
                request(base_url, invitation_target, 200, headers=member_bearer)
                request(base_url, "/api/v1/invitations/not-a-uuid", 400, headers=member_bearer)
                _, owned_invites = request(base_url, target + "/invitations?status=pending&limit=1", 200, headers=bearer)
                _, received_invites = request(base_url, "/api/v1/invitations?status=pending&limit=1", 200, headers=member_bearer)
                require([i["id"] for i in json.loads(owned_invites)["items"]] == invitation_ids, "Owner invitation listing differs.")
                require(json.loads(owned_invites) == json.loads(received_invites), "Recipient invitation listing differs.")
                request(base_url, invitation_target + "/cancel", 403, method="POST", headers=member_bearer)
                request(base_url, invitation_target + "/response", 403, method="POST", body={"action": "accept"}, headers=bearer)
                for _ in range(2):
                    _, accepted = request(base_url, invitation_target + "/response", 200, method="POST", body={"action": "accept"}, headers=member_bearer)
                    require(json.loads(accepted)["status"] == "accepted", "Invitation acceptance differs.")
                _, member_list = request(base_url, target + "/members", 200, headers=member_bearer)
                listed = json.loads(member_list)["items"]
                member["membership_id"] = next(item["membership_id"] for item in listed if item["user"]["id"] == member["user_id"])
                require(len(listed) == 2 and all(set(item["user"]) == {"id", "display_name"} for item in listed), "Member list includes private or incorrect data.")
                require(any(item["membership_id"] == member["membership_id"] for item in listed), "Member generation is missing.")
                _, searched = request(base_url, target + "/members?q=sMoKe%20mEmBeR", 200, headers=bearer)
                require([item["membership_id"] for item in json.loads(searched)["items"]] == [member["membership_id"]], "Display-name search differs.")
                request(base_url, target + "/members/" + actor["user_id"], 403, method="DELETE", headers=member_bearer)
                request(base_url, target + "/members/" + actor["user_id"], 409, method="DELETE", headers=bearer)
                request(base_url, target + "/membership", 409, method="DELETE", headers=bearer)
                removal_path = target + "/members/" + member["user_id"]
                for _ in range(2):
                    _, removed = request(base_url, removal_path, 204, method="DELETE", headers=bearer)
                    require(removed == "", "Removal must be bodyless.")
                request(base_url, target + "/members", 404, headers=member_bearer)
                request(base_url, target, 404, headers=member_bearer)
                request(base_url, invitation_target + "/response", 200, method="POST", body={"action": "accept"}, headers=member_bearer)
                request(base_url, target + "/members", 404, headers=member_bearer)
                replacement_target = invite_member()
                request(base_url, replacement_target + "/response", 200, method="POST", body={"action": "accept"}, headers=member_bearer)
                _, replacement_members = request(base_url, target + "/members", 200, headers=member_bearer)
                replacement = next(item for item in json.loads(replacement_members)["items"] if item["user"]["id"] == member["user_id"])
                require(replacement["membership_id"] != member["membership_id"], "Rejoin reused a revoked generation.")
                request(base_url, target + "/members", 200, headers=member_bearer)
                _, left = request(base_url, target + "/membership", 204, method="DELETE", headers=member_bearer)
                require(left == "", "Leave must be bodyless.")
                request(base_url, target + "/membership", 404, method="DELETE", headers=member_bearer)
                request(base_url, target + "/members", 404, headers=member_bearer)
                declined_target = invite_member()
                for _ in range(2):
                    _, declined = request(base_url, declined_target + "/response", 200, method="POST", body={"action": "decline"}, headers=member_bearer)
                    require(json.loads(declined)["status"] == "declined", "Repeated decline differs.")
                request(base_url, declined_target + "/cancel", 409, method="POST", headers=bearer)
                cancelled_target = invite_member()
                for _ in range(2):
                    _, cancelled = request(base_url, cancelled_target + "/cancel", 200, method="POST", headers=bearer)
                    require(json.loads(cancelled)["status"] == "cancelled", "Repeated cancellation differs.")
                request(base_url, cancelled_target + "/response", 409, method="POST", body={"action": "accept"}, headers=member_bearer)
                pending_target = invite_member()
                record("Invitation create/retry, owner/recipient lists/detail, accept/decline/cancel, terminal retries and accepted-after-leave replay verified")
                current_headers, current_body = request(base_url, target, 200, headers=bearer)
                require(json.loads(current_body)["member_count"] == 1, "Member revocation did not update the workspace.")
                deletion_version = json.loads(current_body)["version"] + 1
                record("Member listing/search, owner boundaries, remove/repeat, rejoin generations, leave/repeat and immediate revocation verified")
                request(base_url, target, 412, method="DELETE", headers={**bearer, "If-Match": '"1"'})
                _, deleted = request(base_url, target, 204, method="DELETE", headers={**bearer, "If-Match": current_headers["ETag"]})
                require(deleted == "", "Delete response must be bodyless.")
                request(base_url, target, 404, headers=bearer)
                request(base_url, pending_target, 404, headers=member_bearer)
                _, hidden_invitations = request(base_url, "/api/v1/invitations", 200, headers=member_bearer)
                require(json.loads(hidden_invitations) == {"items": [], "next_cursor": None}, "Deleted workspace invitations remain visible.")
                request(base_url, "/api/v1/workspaces", 404, method="POST", body={"name": "Smoke Team"}, headers=create_headers)
                _, me = request(base_url, "/api/v1/me", 200, headers=bearer)
                require(json.loads(me)["owned_workspace_count"] == 0, "Deletion did not release ownership quota.")
                lifecycle = json.loads(docker.compose("exec", "--no-TTY", "identity", "python", "-c", "\n".join([
                    "import json",
                    "from uuid import UUID",
                    "from sqlalchemy import select, func",
                    "from identity_workspace.config import load_settings",
                    "from identity_workspace.database import create_database",
                    "from identity_workspace.persistence import outbox, memberships, invitations",
                    "db = create_database(load_settings())",
                    "with db.transaction() as s:",
                    " events = s.execute(select(outbox).order_by(outbox.c.created_at, outbox.c.event_id)).mappings().all()",
                    " active = s.scalar(select(func.count()).select_from(memberships).where(memberships.c.revoked_at.is_(None)))",
                    " deleted = [event for event in events if event['event_type'] == 'workspace.deleted.v1']",
                    " removals = [event for event in events if event['event_type'] == 'workspace.member_removed.v1']",
                    " created = [event for event in events if event['event_type'] == 'invitation.created.v1']",
                    " result = {'deletion_versions': [e['payload']['aggregate_version'] for e in deleted], 'all_pending': all(e['published_at'] is None for e in events), 'active_memberships': active, 'removals': [{'membership_id': e['payload']['data']['membership_id'], 'reason': e['payload']['data']['reason'], 'aggregate_id': e['payload']['aggregate_id'], 'aggregate_version': e['payload']['aggregate_version']} for e in removals], 'invitation_ids': sorted(e['payload']['data']['invitation_id'] for e in created), 'invitation_aggregates_valid': all(e['payload']['aggregate_id'] == e['payload']['data']['invitation_id'] and e['payload']['aggregate_version'] == 1 for e in created), 'invitation_states': sorted(s.scalar(select(invitations.c.status).where(invitations.c.id == UUID(e['payload']['data']['invitation_id']))) for e in created)}",
                    "db.close()",
                    "print(json.dumps(result))",
                ])))
                require(lifecycle == {
                    "deletion_versions": [deletion_version], "all_pending": True, "active_memberships": 0,
                    "removals": [{"membership_id": member["membership_id"], "reason": "removed", "aggregate_id": member["membership_id"], "aggregate_version": 2},
                                 {"membership_id": replacement["membership_id"], "reason": "left", "aggregate_id": replacement["membership_id"], "aggregate_version": 2}],
                    "invitation_ids": sorted(invitation_ids), "invitation_aggregates_valid": True,
                    "invitation_states": ["accepted", "accepted", "cancelled", "cancelled", "declined"],
                }, "Generation-specific removal/deletion outbox state differs.")
                record("Authenticated workspace create/retry/list/read/rename/delete, preconditions, quota release, and durable deletion event verified without a broker")
                credential = f"synthetic-smoke-secret-{uuid4().hex}"
                _, error_body = request(base_url, f"/missing/{credential}", 404)
                _, query_error = request(base_url, f"/health/live?credential={credential}", 400)
                require(credential not in error_body + query_error, "An error response echoed sensitive input.")

                docker.compose("stop", "--timeout", "10", "db")
                outage_metrics = health(base_url, 503)
                record("Database outage: live/metrics remain 200 and ready becomes 503")
                docker.compose("up", "--detach", "--wait", "--wait-timeout", "60", "db")
                recovered_metrics = health(base_url, 200)
                require(docker.compose("ps", "--quiet", "identity") == identity_id, "Application was unexpectedly recreated.")
                record("Readiness recovers to 200 without restarting the application")

                docker.compose("stop", "--timeout", "20", "identity")
                stopped = json.loads(docker.run("inspect", "--format", "{{json .State}}", identity_id))
                results["shutdown_exit_code"] = stopped["ExitCode"]
                # Uvicorn completes lifespan shutdown, then re-raises SIGTERM.
                # Require completed cleanup below as well as the expected exit;
                # a forced SIGKILL (137), OOM, or shutdown error must fail.
                require(stopped["ExitCode"] in {0, 143} and not stopped["OOMKilled"] and not stopped["Error"], "Application did not stop cleanly.")
                logs = docker.compose("logs", "--no-color", "--no-log-prefix", "identity", "migrate")
                require(not any(secret in logs for secret in (*docker.secrets, credential, google_probe)), "Container logs contain sensitive input.")
                require(credential not in outage_metrics + recovered_metrics, "Metrics contain sensitive input.")
                events = [json.loads(line) for line in logs.splitlines() if line.startswith("{")]
                event_names = {item.get("event") for item in events}
                require({"application_started", "application_stopped", "http_request_completed"} <= event_names, "Structured lifecycle/request logs are missing.")
                require("application_shutdown_failed" not in event_names and "Application shutdown complete." in logs, "Application shutdown cleanup did not complete.")
                record("DEBUG application logs and metrics exclude credentials; SIGTERM completes application cleanup")
            finally:
                docker.compose("down", "--volumes", "--remove-orphans")
                record("Disposable test containers, network, and database volume removed")
        results["status"] = "passed"
    except Exception:
        results["status"] = "failed"
        raise
    finally:
        results["verified_at"] = datetime.now(timezone.utc).isoformat()
        report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="identity-workspace:local")
    parser.add_argument("--skip-build", action="store_true", help="Verify an already built image.")
    parser.add_argument("--report", type=Path, default=SERVICE_ROOT / "test-results/container-smoke.json")
    args = parser.parse_args()
    try:
        smoke(args.image, args.skip_build, args.report)
    except SmokeFailure as error:
        print(f"FAIL: {error}", flush=True)
        return 1
    except (OSError, subprocess.TimeoutExpired, urllib.error.URLError):
        print("FAIL: Docker, filesystem, or HTTP operation failed; check Docker Desktop and retry.", flush=True)
        return 1
    print("Container smoke test passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
