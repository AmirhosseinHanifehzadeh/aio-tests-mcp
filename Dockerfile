FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
# The version is derived from git metadata; supply it so the build is reproducible.
ARG VERSION=0.0.0
ENV UV_DYNAMIC_VERSIONING_BYPASS=${VERSION}
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
EXPOSE 8000

ENTRYPOINT ["aio-tests-mcp-server"]
CMD ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
