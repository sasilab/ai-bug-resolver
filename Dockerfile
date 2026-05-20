FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Install uv (single static binary via the official installer is overkill in a
# container — pip install is plenty fast for the POC).
RUN pip install --no-cache-dir uv==0.4.27

WORKDIR /app

# Install dependencies first for better layer caching.
COPY mcp-server/pyproject.toml /app/pyproject.toml
RUN uv sync --no-install-project --python /usr/local/bin/python3.12 \
    && rm -rf /root/.cache/uv

# Copy project source.
COPY mcp-server/mcp_server /app/mcp_server
COPY mcp-server/tests /app/tests

# Drop privileges.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 mcp \
    && chown -R mcp:mcp /app
USER mcp

EXPOSE 8000

# Default command runs the webhook HTTP service. The stdio MCP server is
# launched separately by OpenClaw via `uv run python -m mcp_server.server`.
CMD ["uv", "run", "python", "-m", "mcp_server.webhook"]
