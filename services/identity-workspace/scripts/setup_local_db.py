"""Create ignored local Compose secrets/configuration without printing passwords."""

import os
from pathlib import Path
import secrets


def create_secret(path: Path) -> str:
    if not path.exists():
        value = secrets.token_urlsafe(32)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Local password file is empty: {path.name}")
    return value


def main(root: Path | None = None) -> None:
    root = root if root is not None else Path(__file__).resolve().parents[1]
    secret_dir = root / ".secrets"
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    create_secret(secret_dir / "database-owner-password")
    password = create_secret(secret_dir / "database-app-password")
    env_files = [
        (root / ".env", "localhost:5434"),
        (root / ".env.container", "db:5432"),
    ]
    for env_file, address in env_files:
        if env_file.exists():
            print(f"Existing {env_file.name} preserved; verify it targets the intended database.")
            continue
        from urllib.parse import quote

        example = (root / ".env.example").read_text(encoding="utf-8")
        configuration = example.replace("REPLACE_WITH_LOCAL_PASSWORD", quote(password, safe=""))
        configuration = configuration.replace("@localhost:5434/identity_db", f"@{address}/identity_db")
        if env_file.name == ".env.container":
            configuration = (
                "# Generated local container configuration; keep this file out of source control.\n"
                + "\n".join(line for line in configuration.splitlines() if not line.startswith("#"))
                + "\n"
            )
        descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(configuration)
        print(f"Created ignored {env_file.name} for the Compose database on {address}.")
    print("Local database password files are ready in ignored .secrets/. No values are displayed.")


if __name__ == "__main__":
    main()
