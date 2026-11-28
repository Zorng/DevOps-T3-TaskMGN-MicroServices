"""Sample telemetry in each component's real metric names and log format.

Every log line carries "sample": true so sample data is never mistaken for live evidence.
"""
import json
import os
import random
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE = os.environ["SERVICE"]
PORT = int(os.environ.get("PORT", "8000"))
METRICS_PATH = os.environ.get("METRICS_PATH", "/metrics")
REQUEST_ID = os.environ["TRACE_REQUEST_ID"]
EVENT_ID = os.environ["TRACE_EVENT_ID"]
STARTED = time.time()
BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10]


def elapsed():
    return time.time() - STARTED


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def histogram(name, count, total, labels=""):
    lines = [f"# TYPE {name} histogram"]
    sep = "," if labels else ""
    for bound in BUCKETS:
        share = min(1.0, bound / 0.08)
        lines.append(f'{name}_bucket{{{labels}{sep}le="{bound}"}} {int(count * share)}')
    lines.append(f'{name}_bucket{{{labels}{sep}le="+Inf"}} {int(count)}')
    lines.append(f"{name}_sum{{{labels}}} {total}" if labels else f"{name}_sum {total}")
    lines.append(f"{name}_count{{{labels}}} {int(count)}" if labels else f"{name}_count {int(count)}")
    return lines


def metrics():
    t = elapsed()
    if SERVICE == "identity-workspace":
        ok, failed = 6 * t, 0.05 * t
        return [
            "# TYPE identity_http_requests_total counter",
            f'identity_http_requests_total{{method="POST",route="/internal/v1/auth/introspect",status_code="200"}} {ok}',
            f'identity_http_requests_total{{method="GET",route="/health/ready",status_code="503"}} {failed}',
            *histogram("identity_http_request_duration_seconds", ok + failed, (ok + failed) * 0.03,
                       'method="POST",route="/internal/v1/auth/introspect"'),
            "# TYPE identity_readiness_status gauge",
            "identity_readiness_status 1",
        ]
    if SERVICE == "identity-outbox":
        return [
            "# TYPE identity_outbox_pending gauge", "identity_outbox_pending 1",
            "# TYPE identity_outbox_oldest_pending_age_seconds gauge", "identity_outbox_oldest_pending_age_seconds 4",
            "# TYPE identity_outbox_published_total counter", f"identity_outbox_published_total {0.4 * t}",
            "# TYPE identity_outbox_publish_failures_total counter",
            'identity_outbox_publish_failures_total{reason="broker_unavailable"} 0',
            "# TYPE identity_outbox_storage_available gauge", "identity_outbox_storage_available 1",
        ]
    if SERVICE == "task":
        ok, failed = 4 * t, 0.02 * t
        return [
            "# TYPE http_server_requests_seconds summary",
            f'http_server_requests_seconds_count{{method="POST",outcome="SUCCESS",status="201",uri="/api/v1/workspaces/{{workspace_id}}/tasks"}} {ok}',
            f'http_server_requests_seconds_sum{{method="POST",outcome="SUCCESS",status="201",uri="/api/v1/workspaces/{{workspace_id}}/tasks"}} {ok * 0.045}',
            f'http_server_requests_seconds_count{{method="GET",outcome="SERVER_ERROR",status="503",uri="/api/v1/workspaces/{{workspace_id}}/tasks"}} {failed}',
            f'http_server_requests_seconds_sum{{method="GET",outcome="SERVER_ERROR",status="503",uri="/api/v1/workspaces/{{workspace_id}}/tasks"}} {failed * 2}',
            "# TYPE task_outbox_pending gauge", "task_outbox_pending 3",
            "# TYPE task_outbox_oldest_seconds gauge", f"task_outbox_oldest_seconds {330 + t}",
            "# TYPE task_outbox_publish_failures_total counter", f"task_outbox_publish_failures_total {0.1 * t}",
            "# TYPE task_outbox_published_total counter", f"task_outbox_published_total {0.5 * t}",
            "# TYPE task_cleanup_consumed_total counter", f"task_cleanup_consumed_total {0.05 * t}",
            "# TYPE task_cleanup_duplicate_total counter", f"task_cleanup_duplicate_total {0.01 * t}",
            "# TYPE task_cleanup_failures_total counter", "task_cleanup_failures_total 0",
            "# TYPE task_cleanup_retries_total counter", "task_cleanup_retries_total 0",
            "# TYPE task_cleanup_deadletter_total counter", "task_cleanup_deadletter_total 0",
        ]
    if SERVICE == "notification":
        requests = 3 * t
        return [
            "# TYPE notification_http_requests_total counter", f"notification_http_requests_total {requests}",
            "# TYPE notification_http_server_errors_total counter", "notification_http_server_errors_total 0",
            *histogram("notification_http_request_duration_seconds", requests, requests * 0.02),
            "# TYPE notification_consumer_events_total counter",
            f'notification_consumer_events_total{{outcome="created"}} {0.5 * t}',
            'notification_consumer_events_total{outcome="suppressed"} 1',
            f'notification_consumer_events_total{{outcome="duplicate"}} {0.02 * t}',
            "# TYPE notification_consumer_processing_failures_total counter", "notification_consumer_processing_failures_total 2",
            "# TYPE notification_consumer_retries_total counter", "notification_consumer_retries_total 1",
            "# TYPE notification_consumer_dead_letters_total counter", "notification_consumer_dead_letters_total 1",
            "# TYPE notification_consumer_invalid_events_total counter", "notification_consumer_invalid_events_total 1",
        ]
    if SERVICE == "broker":
        depth = {"notifications.v1": 2, "notifications.v1.dlq": 1, "task-cleanup.v1": 0,
                 "task-cleanup.v1.dlq": 0, "notifications.v1.retry.5s": 0, "task-cleanup.v1.retry.5s": 0}
        lines = ["# TYPE rabbitmq_queue_messages gauge"]
        lines += [f'rabbitmq_queue_messages{{vhost="taskmgn",queue="{q}"}} {n}' for q, n in depth.items()]
        return lines
    return []


def log(line):
    line["sample"] = True
    print(json.dumps(line), flush=True)


def trace_lines():
    if SERVICE == "gateway":
        log({"time": now(), "request_id": REQUEST_ID, "remote_addr": "203.0.113.7", "method": "POST",
             "path": "/api/v1/workspaces/00000000-0000-4000-8000-000000000003/tasks", "status": 201,
             "upstream_status": "201", "request_time": 0.061, "bytes": 412})
    elif SERVICE == "task":
        log({"@timestamp": now(), "log": {"level": "INFO", "logger": "com.taskmanagement.task.tasks.TaskService"},
             "service": {"name": "task-service"}, "message": "Task created with assignment", "request_id": REQUEST_ID})
        log({"@timestamp": now(), "log": {"level": "INFO", "logger": "com.taskmanagement.task.outbox.OutboxPublisher"},
             "service": {"name": "task-service"},
             "message": f"Outbox event published event_id={EVENT_ID} request_id={REQUEST_ID} type=task.assigned.v1"})
    elif SERVICE == "identity-workspace":
        log({"timestamp": now(), "service": "identity-workspace", "level": "INFO", "event": "http_request_completed",
             "request_id": REQUEST_ID, "method": "POST", "route": "/internal/v1/workspaces/{workspace_id}/access",
             "status_code": 200, "duration_ms": 8.4})
    elif SERVICE == "notification":
        log({"timestamp": now(), "service": "notification", "level": "warn", "event": "event_retry_scheduled",
             "event_id": EVENT_ID, "request_id": REQUEST_ID, "type": "task.assigned.v1", "attempt": 1, "delay": "5s"})
        log({"timestamp": now(), "service": "notification", "level": "info", "event": "event_processed",
             "event_id": EVENT_ID, "request_id": REQUEST_ID, "type": "task.assigned.v1", "attempt": 2,
             "outcome": "created"})


def routine_lines():
    rid = "00000000-0000-4000-8000-%012x" % random.getrandbits(48)
    if SERVICE == "gateway":
        status = random.choice([200, 200, 200, 200, 502])
        log({"time": now(), "request_id": rid, "remote_addr": "203.0.113.7", "method": "GET",
             "path": "/api/v1/notifications", "status": status,
             "upstream_status": "502" if status == 502 else "200",
             "request_time": round(random.uniform(0.005, 0.2), 3), "bytes": 230})
    elif SERVICE == "notification":
        log({"timestamp": now(), "service": "notification", "level": "info", "event": "request_completed",
             "request_id": rid, "status": 200, "duration_ms": random.randint(2, 40)})
    elif SERVICE == "identity-workspace":
        log({"timestamp": now(), "service": "identity-workspace", "level": "INFO", "event": "http_request_completed",
             "request_id": rid, "method": "POST", "route": "/internal/v1/auth/introspect", "status_code": 200,
             "duration_ms": round(random.uniform(2, 30), 1)})


def leaked_secret_lines():
    if SERVICE == "notification":
        log({"timestamp": now(), "service": "notification", "level": "error", "event": "redaction_probe",
             "detail": "Authorization: Bearer sample-secret-token-123",
             "url": "amqp://notification:sample-broker-password@broker:5672/taskmgn"})


class Metrics(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != METRICS_PATH:
            self.send_error(404)
            return
        body = ("\n".join(metrics()) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def emit_logs():
    while True:
        trace_lines()
        leaked_secret_lines()
        for _ in range(5):
            routine_lines()
            time.sleep(1)


if __name__ == "__main__":
    threading.Thread(target=emit_logs, daemon=True).start()
    if SERVICE == "gateway":
        threading.Event().wait()
        sys.exit(0)
    ThreadingHTTPServer(("0.0.0.0", PORT), Metrics).serve_forever()
