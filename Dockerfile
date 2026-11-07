# syntax=docker/dockerfile:1

# Base images are pinned by digest (tags can be moved); Dependabot keeps them current.
# The uv version must equal `required-version` in pyproject.toml, or the build fails.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# --- build: resolve the locked environment with uv ---
FROM python:3.15-slim@sha256:e97b88e416c3c01eb427b21fd8cd3ebf006e6ee38afcfcc3cae1c54ee2ab0a75 AS builder
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
FROM python:3.15-slim@sha256:e97b88e416c3c01eb427b21fd8cd3ebf006e6ee38afcfcc3cae1c54ee2ab0a75
# The app never installs packages at runtime, so drop the base image's pip and its bundled
# wheel: less attack surface, and they carry vendored libraries (urllib3, msgpack, ...) that
# Trivy flags and we can't patch.
# zlib1g comes from trixie-security until the base image ships it: CVE-2026-76844 and
# CVE-2026-85091 are fixed in 1:1.3.dfsg+really1.3.1-1+deb13u1, and the newest
# python:3.15-slim digest (2026-11-05) still has +b1. Drop the apt lines once Dependabot
# bumps the digest to one that has deb13u1 (`dpkg-query -W zlib1g` in the image).
RUN apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes --quiet pip \
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
