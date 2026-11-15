FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

FROM python:3.13.16-slim-bookworm@sha256:73aa5faee130c60da030e105ec971ccf57473fbd22ce73bd6fc9ba99a40b838d AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
WORKDIR /app

COPY .python-version pyproject.toml uv.lock ./
COPY docs/environment-setup.md ./docs/environment-setup.md
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --python /usr/local/bin/python

FROM python:3.13.16-slim-bookworm@sha256:73aa5faee130c60da030e105ec971ccf57473fbd22ce73bd6fc9ba99a40b838d AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 identity \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin identity

COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/alembic.ini /app/alembic.ini
COPY --from=build /app/migrations /app/migrations
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).close()"]
CMD ["uvicorn", "identity_workspace.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--no-proxy-headers", "--timeout-keep-alive", "5", "--timeout-graceful-shutdown", "10"]
