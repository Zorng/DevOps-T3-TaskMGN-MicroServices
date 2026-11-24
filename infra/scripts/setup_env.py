#!/usr/bin/env python3
"""Create infra/.env and infra/.secrets/ for the local application stack.

Run from the repository root:  python3 infra/scripts/setup_env.py

Existing files are never overwritten, so re-running is safe and does not change
passwords that a database volume already depends on. No secret is printed.
"""

import os
import secrets
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parents[1]
EXAMPLE = INFRA / ".env.example"
ENV_FILE = INFRA / ".env"
SECRETS_DIR = INFRA / ".secrets"
PLACEHOLDER = "generate"

# File name in .secrets/ -> variable in .env. Identity's database image reads
# its passwords from files, so these two values are also written as files.
SECRET_FILES = {
    "identity-db-owner-password": "IDENTITY_DB_OWNER_PASSWORD",
    "identity-db-app-password": "IDENTITY_DB_APP_PASSWORD",
}


def create_env_file():
    if ENV_FILE.exists():
        print("infra/.env already exists; left unchanged.")
        return
    lines = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and not name.startswith("#") and value.strip() == PLACEHOLDER:
            # 32 random bytes -> 43 URL-safe characters (valid for every service).
            line = f"{name}={secrets.token_urlsafe(32)}"
        lines.append(line)
    descriptor = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    print("Created infra/.env with generated secrets.")


def read_env_values():
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name, _, value = stripped.partition("=")
            values[name.strip()] = value.strip()
    return values


def create_secret_files():
    values = read_env_values()
    SECRETS_DIR.mkdir(mode=0o700, exist_ok=True)
    SECRETS_DIR.chmod(0o700)
    for filename, variable in SECRET_FILES.items():
        path = SECRETS_DIR / filename
        if path.exists():
            continue
        value = values.get(variable, "")
        if not value or value == PLACEHOLDER:
            raise ValueError(f"{variable} in infra/.env is empty or still a placeholder.")
        # No trailing newline. The directory is owner-only; the file is readable
        # so the database container's non-root user can read the mounted copy.
        path.write_text(value, encoding="utf-8")
        path.chmod(0o644)
        print(f"Created infra/.secrets/{filename}.")


def main():
    try:
        create_env_file()
        create_secret_files()
    except (OSError, ValueError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        return 1
    print("Local configuration ready. Edit infra/.env to add IDENTITY_GOOGLE_CLIENT_ID.")
    return 0


if __name__ == "__main__":
    sys.exit(main())