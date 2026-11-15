import importlib.util
from pathlib import Path

import pytest

from identity_workspace.config import load_settings

SERVICE_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("setup_local_db", SERVICE_ROOT / "scripts/setup_local_db.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_repeated_setup_preserves_credentials_and_configuration(tmp_path, capsys):
    (tmp_path / ".env.example").write_bytes((SERVICE_ROOT / ".env.example").read_bytes())
    setup.main(tmp_path)
    env_before = (tmp_path / ".env").read_bytes()
    container_env_before = (tmp_path / ".env.container").read_bytes()
    owner_before = (tmp_path / ".secrets/database-owner-password").read_bytes()
    app_before = (tmp_path / ".secrets/database-app-password").read_bytes()
    setup.main(tmp_path)
    assert (tmp_path / ".env").read_bytes() == env_before
    assert (tmp_path / ".env.container").read_bytes() == container_env_before
    assert (tmp_path / ".secrets/database-owner-password").read_bytes() == owner_before
    assert (tmp_path / ".secrets/database-app-password").read_bytes() == app_before
    assert owner_before != app_before
    output = capsys.readouterr().out
    assert owner_before.decode() not in output
    assert app_before.decode() not in output
    assert ":5434/identity_db" in load_settings(env_file=tmp_path / ".env").database_url.get_secret_value()
    container_url = load_settings(env_file=tmp_path / ".env.container").database_url.get_secret_value()
    assert "@db:5432/identity_db" in container_url
    assert app_before.decode() in container_url


def test_setup_preserves_existing_user_configuration(tmp_path):
    (tmp_path / ".env.example").write_bytes((SERVICE_ROOT / ".env.example").read_bytes())
    existing = "IDENTITY_ENVIRONMENT=production\n"
    (tmp_path / ".env").write_text(existing, encoding="utf-8")
    setup.main(tmp_path)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == existing


def test_setup_preserves_existing_container_configuration(tmp_path):
    (tmp_path / ".env.example").write_bytes((SERVICE_ROOT / ".env.example").read_bytes())
    existing = "IDENTITY_ENVIRONMENT=production\n"
    (tmp_path / ".env.container").write_text(existing, encoding="utf-8")
    setup.main(tmp_path)
    assert (tmp_path / ".env.container").read_text(encoding="utf-8") == existing
    assert (tmp_path / ".env").exists()


def test_empty_existing_password_is_rejected(tmp_path):
    secret_dir = tmp_path / ".secrets"
    secret_dir.mkdir()
    (secret_dir / "database-owner-password").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="password file is empty"):
        setup.main(tmp_path)
