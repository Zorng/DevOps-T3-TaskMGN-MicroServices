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
                    " 'development_packages': sorted(installed & {'pytest', 'httpx2', 'uv', 'uv-build'}),",
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
