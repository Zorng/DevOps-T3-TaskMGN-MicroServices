# Identity & Workspace Environment Setup

This document describes the Python package and locked development environment. See [application startup](application-startup.md) for configuration/lifecycle tests, [database setup](database-setup.md) for PostgreSQL/migrations, and [health probes](health-probes.md) for the implemented operational endpoints. Business HTTP endpoints remain subsequent work.

## Prerequisites

- Python 3.13.16, 64-bit. The service supports Python 3.13 patch releases starting with 3.13.16; `.python-version` selects 3.13.16 for consistent local/CI/container setup.
- uv 0.13.8, enforced by `tool.uv.required-version` in `pyproject.toml`.
- Network access to PyPI for the initial installation. No database, broker, Google account, or application secrets are needed for this step.

## Install on Windows PowerShell

From the project root, enter the service directory:

```powershell
Set-Location services/identity-workspace
python --version
```

If uv 0.13.8 is already installed and on PATH, skip the bootstrap below and use `uv` in place of `& $identityUv` in subsequent commands. Otherwise, install a service-local copy without changing globally installed Python packages:

```powershell
python -m pip --isolated install --index-url https://pypi.org/simple --no-cache-dir --no-compile --target .tools/uv uv==0.13.8
if ($LASTEXITCODE -ne 0) { throw "uv installation failed." }
$identityUv = Join-Path $PWD '.tools/uv/bin/uv.exe'
& $identityUv --version
```

The `.tools/` directory is ignored by Git. The following commands keep the package cache inside the service too:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
& $identityUv sync --locked
if ($LASTEXITCODE -ne 0) { throw "Locked dependency installation failed." }
```

This creates an ignored `.venv/` and installs the service package plus runtime/development dependencies. Virtual environment activation is unnecessary when using uv. Python downloads are disabled in project settings: install the required interpreter explicitly if it is missing.

## Verify the environment

```powershell
& $identityUv lock --check
& $identityUv pip check
& $identityUv run --locked python -c "import identity_workspace, fastapi, pydantic, pydantic_settings, uvicorn, sqlalchemy, psycopg, alembic, prometheus_client, pytest, httpx2; print('Service package and dependencies import successfully.')"
& $identityUv run --locked pytest --version
```

Each command must exit successfully. `pytest --version` verifies installation only; use the separate suite commands in [application startup](application-startup.md#verification-commands) and [database setup](database-setup.md#integration-tests) to run the real tests.

To verify the package can be built independently:

```powershell
& $identityUv build --no-sources
if ($LASTEXITCODE -ne 0) { throw "Package build failed." }
```

Generated `dist/` artifacts are ignored. This builds Python distribution artifacts, not the future service container.

## Runtime-only installation

CI/container implementation will use the same pinned uv and Python versions. From the service directory, a runtime-only installation uses:

```text
uv sync --locked --no-dev --no-editable
```

The lockfile includes both runtime and development groups; `--no-dev` selects only the runtime environment, and `--no-editable` installs the service as a built package. A normal `uv sync --locked` restores the development environment.

## Verification recorded for this step

Verified on Windows with Python 3.13.16 and uv 0.13.8:

- `uv lock --check --offline`: lockfile matches the project metadata.
- `uv sync --locked`: the development environment installs successfully.
- `uv pip check`: installed package requirements are consistent.
- The service package and all selected runtime/development libraries import successfully; pytest reports version 9.2.0.
- `uv sync --locked --no-dev --no-editable --offline`: the runtime-only package installs from a built wheel and imports successfully; development test tools are absent. The development environment was restored afterward.
- `uv build --no-sources --offline`: source and wheel distributions build successfully. Archive contents were checked to exclude environment files, caches, local tools, and generated reports.

These checks verify package/environment setup. Step 5 updates the test client to HTTPX2 for the selected Starlette version; see the [startup guide](application-startup.md) for current unit/request verification. Package setup does not establish passing health/database tests, a container smoke-test result, or a passing GitHub Actions pipeline.

## Dependency updates

Commit `pyproject.toml`, `uv.lock`, `.python-version`, and relevant documentation together. Normal setup uses the existing lockfile; only intentional dependency changes regenerate it. After a change, rerun lock consistency, dependency/import verification, and the service suites available at that stage. Upgrade the pinned uv build backend and tool version together.

Do not commit `.venv/`, `.tools/`, `.uv-cache/`, Python caches, build artifacts, reports, or real `.env` files. The original service README is retained; these detailed instructions live separately as requested.

Related documents: [stack decisions](stack-decisions.md), [contract requirements](contract-requirements.md), and [scaffold scope](scaffold-scope.md).
