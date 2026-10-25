# syntax=docker/dockerfile:1

# Base images are pinned by digest (tags can be moved); Dependabot keeps them current.
# The uv version must equal `required-version` in pyproject.toml, or the build fails.
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

# --- build: resolve the locked environment with uv ---
FROM python:3.15-slim@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51 AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- runtime: just Python and the virtualenv, running as non-root ---
FROM python:3.15-slim@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51
# The app never installs packages at runtime, so drop the base image's pip and its bundled
# wheel: less attack surface, and they carry vendored libraries (urllib3, msgpack, ...) that
# Trivy flags and we can't patch.
RUN python -m pip uninstall --yes --quiet pip \
    && rm -rf /usr/local/lib/python3.*/ensurepip \
    && groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir /data && chown app:app /data
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    LOG_FORMAT=json
USER app
VOLUME ["/data"]
ENTRYPOINT ["garminagent"]
CMD ["--help"]
