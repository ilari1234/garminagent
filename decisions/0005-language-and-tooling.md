# 0005: Python 3.15, uv, httpx, strict typing

**Status:** Accepted.

## Context

The owner required Python and easy containerization. Everything else was open.

## Decision

- **Python 3.15** (owner's change from the proposed 3.12), `python:3.15-slim` images.
- **uv**, pinned exactly with `required-version`; lock file with hashes, installed `--locked`.
- **httpx** for the Garmin client.
- **ruff** including bandit rules, **pyright strict**, **pytest** with fakes and synthetic fixtures.
- **pydantic-settings** for configuration: environment variables only, secrets as `SecretStr`.
- **Typer** CLI; every command prints JSON on stdout, logs go to stderr.

## Alternatives considered

- **Python 3.12.** Proposed first; replaced at the owner's request.
- **`requests`.** Rejected: httpx has `MockTransport` for fake-server tests, default timeouts, type
  hints, sync/async parity and matches the Anthropic SDK. Its downside is being pre-1.0.

## Consequences

- Python 3.15 is new, so tools lag: Dependabot's uv updates fail until it supports 3.15, and GitHub's
  dependency graph can't process `uv.lock`. Hence a monthly manual `uv lock --upgrade`.
- A uv version change touches `required-version`, the Dockerfile uv image and the `uv_build` bound
  together.
- No live network calls in tests or CI.
