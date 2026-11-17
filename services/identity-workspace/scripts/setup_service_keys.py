"""Provision ignored local service keys; preserve existing files and never print values."""
import os
from pathlib import Path
import re
import secrets


def existing_value(path):
    failed = False
    try:
        with path.open("rb") as stream:
            raw = stream.read(131)
        value = raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
        if len(raw) > 130 or re.fullmatch(r"[A-Za-z0-9_-]{43,128}", value) is None:
            failed = True
    except (OSError, UnicodeError):
        failed = True
    if failed:
        raise ValueError("Invalid local service-key file; values omitted.") from None
    return value


def main(root=None):
    root = root if root is not None else Path(__file__).resolve().parents[1]
    directory = root / ".secrets"
    directory.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    values = []
    for name in ("task-service-key", "notification-service-key"):
        path = directory / name
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(secrets.token_urlsafe(32))
        value = existing_value(path)
        values.append(value)
        if os.name != "nt":
            # Compose file secrets are bind mounts and retain host permissions.
            # Private parent 0700 blocks host traversal; 0644 lets container UID
            # 10001 read its individually mounted, read-only key files.
            path.chmod(0o644)
    if secrets.compare_digest(*values):
        raise ValueError("Task and Notification keys must be distinct; values omitted.")
    print("Ignored per-service key files are ready. Existing files preserved; no values displayed.")


if __name__ == "__main__":
    main()
