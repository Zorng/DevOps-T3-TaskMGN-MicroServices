#!/usr/bin/env python3
"""Back up the shared stack and PROVE the backup restores, without touching live data.

  python3 infra/scripts/backup_restore.py backup                 # write a backup set
  python3 infra/scripts/backup_restore.py verify [BACKUP_DIR]    # restore it in isolation and compare
  python3 infra/scripts/backup_restore.py drill                  # marker -> backup -> verify -> clean up

What is backed up (see infra/README.md for scope, retention and restore steps):
  - the Identity, Task and Notification databases (pg_dump custom format + a manifest of
    row counts per table),
  - the broker topology: exchanges, queues, bindings, users and permissions
    (rabbitmqctl export_definitions). Queued messages are NOT backed up: services
    republish from their outboxes.

Verification starts throw-away containers with NO network and NO published ports,
restores into them, compares every table's row count with the manifest and removes
the containers. Live containers and volumes are only ever read.

Backup sets contain credentials (broker users), so they are written with private
permissions into infra/.backups/ (ignored by Git). Standard library only.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

INFRA = pathlib.Path(__file__).resolve().parent.parent
BACKUPS = INFRA / ".backups"
COMPOSE = ["docker", "compose", "-f", str(INFRA / "compose.yaml")]
DATABASES = {"identity": "identity-db", "task": "task-db", "notification": "notification-db"}
VHOST = "taskmgn"
DRILL_TABLE = "restore_drill"

results = []


def record(status, name, detail=""):
    results.append(status)
    print(f"{status:<8} {name}" + (f"  ({detail})" if detail else ""))


def check(name, ok, detail=""):
    record("PASS" if ok else "FAIL", name, "" if ok else detail)
    return ok


# ---- Docker layer (thin, so everything below it can be tested without Docker) --------

def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


class Throwaway:
    """A short-lived container with no published ports."""

    # `docker exec` runs as root unless told otherwise. Subclasses whose server must
    # own its own files set this to the account the server runs as.
    EXEC_USER = None

    def __init__(self, image, env, tag, network="none"):
        self.name = f"taskmgn-restore-{tag}-{os.getpid()}-{int(time.time())}"
        args = ["docker", "run", "-d", "--network", network, "--name", self.name]
        for key, value in env.items():
            args += ["-e", f"{key}={value}"]
        result = run([*args, image])
        if result.returncode != 0:
            raise SystemExit(f"cannot start throw-away container: {result.stderr.strip()[-300:]}")

    def exec(self, *command, user=None):
        account = user or self.EXEC_USER
        prefix = ["-u", account] if account else []
        return run(["docker", "exec", *prefix, self.name, *command])

    def copy_in(self, source, destination):
        result = run(["docker", "cp", str(source), f"{self.name}:{destination}"])
        if result.returncode != 0:
            raise SystemExit(f"docker cp failed: {result.stderr.strip()[-200:]}")

    def logs(self, lines=20):
        """Container state and the end of its log (the container is kept until remove())."""
        state = run(["docker", "inspect", "-f", "state={{.State.Status}} exit_code={{.State.ExitCode}}", self.name])
        result = run(["docker", "logs", "--tail", str(lines), self.name])
        return (state.stdout.strip() + "\n" + (result.stdout + result.stderr).strip()).strip()

    def remove(self):
        run(["docker", "rm", "-f", self.name])


class PostgresThrowaway(Throwaway):
    def __init__(self, image, user, database):
        self.user, self.database = user, database
        super().__init__(image, {"POSTGRES_USER": user, "POSTGRES_DB": database,
                                 "POSTGRES_PASSWORD": "restore-drill-only"}, "pg")

    def ready(self, tries=60):
        for _ in range(tries):
            if self.exec("pg_isready", "-U", self.user, "-d", self.database).returncode == 0:
                return True
            time.sleep(2)
        return False

    def restore(self, dump):
        self.copy_in(dump, "/tmp/restore.dump")
        return self.exec("pg_restore", "-U", self.user, "-d", self.database,
                         "--no-owner", "--no-privileges", "/tmp/restore.dump")

    def sql(self, statement):
        result = self.exec("psql", "-U", self.user, "-d", self.database, "-At", "-c", statement)
        if result.returncode != 0:
            raise SystemExit(f"restored database: SQL failed: {result.stderr.strip()[-200:]}")
        return result.stdout


class BrokerThrowaway(Throwaway):
    """RabbitMQ will not start with no network at all, so it gets a private INTERNAL
    Docker network: no route to the outside and no published ports.

    Every command runs as the `rabbitmq` account. Run as root, the first
    `rabbitmq-diagnostics` poll can create /var/lib/rabbitmq/.erlang.cookie owned by
    root before the server starts, and the server (running as rabbitmq) then crashes
    with "eacces" reading it."""

    EXEC_USER = "rabbitmq"

    def __init__(self, image):
        self.network = f"taskmgn-restore-net-{os.getpid()}-{int(time.time())}"
        created = run(["docker", "network", "create", "--internal", self.network])
        if created.returncode != 0:
            raise SystemExit(f"cannot create the isolated network: {created.stderr.strip()[-200:]}")
        try:
            super().__init__(image, {}, "mq", network=self.network)
        except BaseException:
            run(["docker", "network", "rm", self.network])
            raise

    def remove(self):
        super().remove()
        run(["docker", "network", "rm", self.network])

    def ready(self, tries=60):
        for _ in range(tries):
            if self.exec("rabbitmq-diagnostics", "-q", "check_running").returncode == 0:
                return True
            time.sleep(3)
        return False

    def import_definitions(self, path):
        self.copy_in(path, "/tmp/defs.json")
        # docker cp leaves the file owned by root with the backup's private (0600)
        # mode; hand it to the account that reads it.
        self.exec("chown", "rabbitmq", "/tmp/defs.json", user="root")
        return self.exec("rabbitmqctl", "import_definitions", "/tmp/defs.json")

    def json(self, *command):
        result = self.exec("rabbitmqctl", "-q", "--formatter", "json", *command)
        if result.returncode != 0:
            raise SystemExit(f"restored broker: {' '.join(command)} failed: {result.stderr.strip()[-200:]}")
        return json.loads(result.stdout)


class Docker:
    """Everything that reads the LIVE stack. It never writes to live data volumes."""

    def exec_live(self, service, script, *args):
        result = run([*COMPOSE, "exec", "-T", service, "sh", "-c", script, *args])
        if result.returncode != 0:
            raise SystemExit(f"{service}: command failed: {result.stderr.strip()[-300:]}")
        return result.stdout

    def psql(self, service, statement):
        return self.exec_live(service, 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$0"', statement)

    def identity(self, service):
        user, _, database = self.exec_live(service, 'printf "%s|%s" "$POSTGRES_USER" "$POSTGRES_DB"').partition("|")
        return user.strip(), database.strip()

    def dump(self, service, destination):
        with open(destination, "wb") as handle:
            result = subprocess.run(
                [*COMPOSE, "exec", "-T", service, "sh", "-c",
                 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom'],
                stdout=handle, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise SystemExit(f"{service}: pg_dump failed: {result.stderr.decode()[-300:]}")

    def broker_definitions(self):
        return self.exec_live("broker", "rabbitmqctl export_definitions /tmp/defs.json >/dev/null && cat /tmp/defs.json && rm -f /tmp/defs.json")

    def broker_json(self, *command):
        return json.loads(self.exec_live("broker", "rabbitmqctl -q --formatter json " + " ".join(command)))

    def image(self, service):
        out = run([*COMPOSE, "config", "--format", "json"]).stdout
        return json.loads(out)["services"][service]["image"]

    def running(self, service):
        return bool(run([*COMPOSE, "ps", "--status", "running", "-q", service]).stdout.strip())

    def postgres_throwaway(self, image, user, database):
        return PostgresThrowaway(image, user, database)

    def broker_throwaway(self, image):
        return BrokerThrowaway(image)


docker = Docker()


# ---- Logic -----------------------------------------------------------------------

def parse_count_rows(output):
    counts = {}
    for line in output.splitlines():
        if "|" in line:
            table, _, count = line.rpartition("|")
            counts[table] = int(count)
    return counts


def table_counts(sql):
    """Exact row count of every user table. `sql(statement)` returns psql -At output."""
    listing = sql(
        "SELECT quote_ident(table_schema) || '.' || quote_ident(table_name) FROM information_schema.tables "
        "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY 1")
    tables = [line.strip() for line in listing.splitlines() if line.strip()]
    if not tables:
        return {}
    union = " UNION ALL ".join(f"SELECT '{t}' || '|' || count(*) FROM {t}" for t in tables)
    return parse_count_rows(sql(union))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_private(path, data):
    mode = "wb" if isinstance(data, bytes) else "w"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, mode) as handle:
        handle.write(data)


def do_backup():
    for service in (*DATABASES.values(), "broker"):
        if not docker.running(service):
            raise SystemExit(f"{service} is not running: start the stack first")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    BACKUPS.chmod(0o700)
    target = BACKUPS / stamp
    target.mkdir(mode=0o700)
    manifest = {"created": stamp, "databases": {}, "broker": {}}

    for name, service in DATABASES.items():
        user, database = docker.identity(service)
        dump = target / f"{name}.dump"
        docker.dump(service, dump)
        dump.chmod(0o600)
        counts = table_counts(lambda statement, s=service: docker.psql(s, statement))
        manifest["databases"][name] = {
            "service": service, "user": user, "database": database, "image": docker.image(service),
            "server_version_num": docker.psql(service, "SHOW server_version_num").strip(),
            "dump": dump.name, "bytes": dump.stat().st_size, "sha256": sha256(dump), "row_counts": counts,
        }
        print(f"backed up {name}: {len(counts)} tables, {dump.stat().st_size} bytes")

    write_private(target / "broker-definitions.json", docker.broker_definitions())
    manifest["broker"] = {
        "image": docker.image("broker"), "definitions": "broker-definitions.json",
        "sha256": sha256(target / "broker-definitions.json"),
        "queues": sorted(q["name"] for q in docker.broker_json("list_queues", "-p", VHOST, "name")),
        "users": sorted(u["user"] for u in docker.broker_json("list_users")),
        "permissions": normalize_permissions(docker.broker_json("list_permissions", "-p", VHOST)),
    }
    write_private(target / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(f"backup set written to {target}")
    return target


def verify_database(name, entry, target, expect_marker=None):
    dump = target / entry["dump"]
    check(f"{name}: dump file matches its recorded checksum", sha256(dump) == entry["sha256"])
    box = docker.postgres_throwaway(entry["image"], entry["user"], entry["database"])
    try:
        if not check(f"{name}: isolated database container is ready (no network, no published ports)", box.ready()):
            print("  container state and last log lines:\n  " + box.logs().replace("\n", "\n  "))
            return
        restored = box.restore(dump)
        check(f"{name}: pg_restore completed without errors", restored.returncode == 0, restored.stderr.strip()[-200:])
        counts = table_counts(box.sql)
        expected = entry["row_counts"]
        check(f"{name}: same tables restored ({len(expected)})", set(counts) == set(expected),
              f"missing={sorted(set(expected) - set(counts))} extra={sorted(set(counts) - set(expected))}")
        for table, rows in sorted(expected.items()):
            if table in counts:
                check(f"{name}: {table} has {rows} rows", counts[table] == rows, f"restored {counts[table]}")
        if expect_marker:
            found = box.sql(f"SELECT marker FROM {DRILL_TABLE}").split()
            check(f"{name}: drill marker row survived the restore", expect_marker in found, f"found={found}")
    finally:
        box.remove()


def normalize_permissions(rows):
    """[(user, configure, write, read)] as sorted lists, so JSON round-trips compare equal."""
    return sorted([r["user"], r["configure"], r["write"], r["read"]] for r in rows)


def verify_broker(entry, target):
    definitions = target / entry["definitions"]
    check("broker: definitions file matches its recorded checksum", sha256(definitions) == entry["sha256"])
    box = docker.broker_throwaway(entry["image"])
    try:
        if not check("broker: isolated RabbitMQ is ready (internal network only, no published ports)", box.ready()):
            print("  container state and last log lines:\n  " + box.logs().replace("\n", "\n  "))
            return
        # A new RabbitMQ container starts with its own default account (guest). Note
        # what it has BEFORE the import, so only accounts that came from the backup
        # are compared.
        baseline = {u["user"] for u in box.json("list_users")}
        imported = box.import_definitions(definitions)
        check("broker: definitions imported", imported.returncode == 0, imported.stderr.strip()[-200:])
        names = sorted(q["name"] for q in box.json("list_queues", "-p", VHOST, "name"))
        check(f"broker: all {len(entry['queues'])} queues recreated", names == entry["queues"],
              f"missing={sorted(set(entry['queues']) - set(names))}")
        restored = {u["user"] for u in box.json("list_users")}
        expected = set(entry["users"])
        check("broker: every backed-up user recreated", expected <= restored,
              f"missing={sorted(expected - restored)}")
        check("broker: no user besides the backed-up ones and the new container's own default",
              restored - expected <= baseline, f"unexpected={sorted(restored - expected - baseline)}")
        if "permissions" in entry:
            permissions = normalize_permissions(box.json("list_permissions", "-p", VHOST))
            check(f"broker: permissions recreated for {len(entry['permissions'])} users on vhost {VHOST}",
                  permissions == entry["permissions"],
                  f"expected={entry['permissions']} restored={permissions}")
        else:
            record("SKIP", "broker: permissions comparison (this backup set predates it)")
    finally:
        box.remove()


def do_verify(target, expect_marker=None):
    manifest = json.loads((target / "manifest.json").read_text())
    print(f"verifying {target.name} in isolated throw-away containers (live data is not touched)\n")
    for name, entry in manifest["databases"].items():
        verify_database(name, entry, target, expect_marker)
        print()
    verify_broker(manifest["broker"], target)
    failed = results.count("FAIL")
    print(f"\n{results.count('PASS')} passed, {failed} failed. Throw-away containers removed; live containers were only read.")
    return 1 if failed else 0


def latest_backup():
    sets = sorted(p for p in BACKUPS.glob("*") if (p / "manifest.json").exists()) if BACKUPS.exists() else []
    if not sets:
        raise SystemExit("no backup set found: run 'backup' first")
    return sets[-1]


def do_drill():
    marker = f"drill-{int(time.time())}"
    for service in DATABASES.values():
        docker.psql(service, f"CREATE TABLE IF NOT EXISTS {DRILL_TABLE} (marker text)")
        docker.psql(service, f"INSERT INTO {DRILL_TABLE} VALUES ('{marker}')")
    try:
        return do_verify(do_backup(), expect_marker=marker)
    finally:
        for service in DATABASES.values():
            docker.psql(service, f"DROP TABLE IF EXISTS {DRILL_TABLE}")
        print("drill marker removed from the live databases")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("backup", "verify", "drill"))
    parser.add_argument("backup_dir", nargs="?", help="backup set to verify (default: the newest)")
    args = parser.parse_args()
    if args.command == "backup":
        do_backup()
        return 0
    if args.command == "verify":
        return do_verify(pathlib.Path(args.backup_dir) if args.backup_dir else latest_backup())
    return do_drill()


if __name__ == "__main__":
    sys.exit(main())