# One image, four containers (three MCP servers + the agent).
FROM python:3.12-slim

# Declared so a proxy can be injected at build time when the machine
# reaches the internet through a local proxy (see RUN.md).
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# uv is installed from PyPI so the build does not depend on ghcr.io.
RUN pip install --no-cache-dir uv==0.11.25

WORKDIR /app

# The project is copied before syncing because the package itself is installed
# (editable console scripts) and hatchling needs src/ present.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY data ./data
COPY scripts ./scripts

RUN uv sync --frozen --no-dev

RUN useradd --create-home --shell /bin/bash appuser \
 && mkdir -p /app/reports \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8101 8102 8103

CMD ["minereport-agent"]

