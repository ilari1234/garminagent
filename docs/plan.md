# garminagent — Plan

AI-assisted training planning built on Garmin Connect data. Agents create, update
and evaluate training plans for off-season training and for events (half marathon,
marathon, ultra, road/gravel cycling), using historical data for planning and
adjustment.

## Decisions

| Topic | Decision |
|---|---|
| Language | Python 3.15, managed with `uv` (`requires-python = ">=3.15"`, `python:3.15-slim` images) |
| Data source | **Garmin Connect Personal API** (official, own data only). No unofficial clients (python-garminconnect/garth). |
| Users | Single user by design — Personal API terms exclude apps serving multiple end users. Schema still carries a `user_id` so it isn't a dead end. |
| Storage | PostgreSQL (JSONB for raw payloads); activity files on a volume, S3-compatible later |
| Runtime | Docker; local Docker Desktop now, designed to move to AWS without code changes |
| Repo | GitHub, private (`ilari1234/garminagent`) |

### AWS portability rules

Applied from day one so "local" and "AWS" differ only by configuration:

- 12-factor config: everything via environment variables (`pydantic-settings`); secrets from env or a mounted file (maps to AWS Secrets Manager / SSM).
- Postgres via a standard `DATABASE_URL` (local container → RDS/Aurora).
- File storage behind a small `BlobStore` interface: local filesystem now, S3 later.
- Sync runs as a **one-shot command** (`garminagent sync`), not a daemon: cron/compose locally → EventBridge Scheduler + ECS Fargate task on AWS.
- Logs as structured JSON to stdout (→ CloudWatch).
- Images built in CI and pushed to a registry (GHCR now, ECR later).

## Garmin Personal API — evaluation summary

Source: developer.garmin.com Personal API pages; spec in
[garmin-personal-api/openapi.json](garmin-personal-api/openapi.json).

- **Auth:** user creates an API key in Garmin Connect web (expiry 30/90/180/365 days, shown once).
  `POST https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token`
  with `Authorization: ApiKey <key>` → access token valid 24 h. No refresh token; request a new one.
- **Shape:** 26 read-only GET endpoints, pull only (no webhooks).
- **Covered:** activity search/summary/splits/workouts, original activity file download, HR zones,
  HRV, sleep, daily summaries (≤1 year per call), stress, steps, Body Battery, SpO2, daily HR,
  weight, hydration, nutrition, fitness age, user settings. Activity summaries include training
  load, aerobic/anaerobic TE, NP, IF, FTP, lactate threshold and VO2max values.
- **Not covered:** Training Status/Readiness, acute/chronic load, race predictor, VO2max trend,
  and any write access (no workout/calendar push). → We compute load/fitness metrics ourselves;
  plans live in our app (later: FIT workout file export).
- **Unknown until measured (M0):** rate limits, history depth, activity file format, real payloads.

## Architecture (phase 1)

```
src/garminagent/
  config.py        settings (env / secret file)
  garmin/          Personal API client: token manager, endpoints, retries, models
  storage/         raw store (BlobStore + raw_payload table), normalized schema, migrations
  sync/            backfill + incremental sync, per-dataset cursors
  cli.py           `garminagent auth-check | sync | backfill`
```

- **Client:** `httpx`; token cached until expiry, re-requested on 401; exponential backoff on
  429/5xx honoring `Retry-After`. Pydantic models generated from the spec, lenient
  (`extra="allow"`) so schema drift doesn't break ingestion.
- **Two storage layers:**
  - *Raw* — every response stored verbatim (endpoint, params, fetched_at, hash) plus original
    activity files. Lets us re-parse without re-fetching.
  - *Normalized* — activities, splits, daily summary, sleep, HRV, body battery, stress, weight,
    plus per-second activity samples parsed from FIT files.
- **Sync:** per-dataset cursor table; daily datasets re-fetch a trailing window (sleep/HRV are
  finalized late); activities via search by start date with paging; idempotent upserts; a
  separate `backfill` command walks history in the largest chunks each endpoint allows.
- **Key expiry:** the key's expiry date is configured; the app warns ahead of time and fails
  clearly on 401 from the token endpoint.
- **Tooling:** ruff, pyright, pytest (+ respx for HTTP mocks, Postgres via testcontainers),
  pre-commit, GitHub Actions (lint, test, build image), multi-stage Dockerfile,
  docker-compose (app + postgres).

## Phase 1 milestones

- **M0 — Spike:** `spikes/probe_personal_api.py` calls every endpoint with the real key and
  records status, latency, size, headers, payload structure, history depth and file format.
  Output → `docs/garmin-personal-api/evaluation.md`.
- **M1 — Scaffold:** pyproject, src layout, ruff/pyright/pytest, CI, Dockerfile, compose.
- **M2 — Client:** token manager, all endpoints, retries, tests with recorded fixtures.
- **M3 — Storage:** SQLAlchemy models, Alembic migrations, raw store.
- **M4 — Sync:** backfill + incremental, activity file download and FIT parsing.
- **M5 — Run it:** scheduled containerized sync, operational docs.

## Later phases (outline)

- **Phase 2 — Analytics:** load (TRIMP/TSS-like), CTL/ATL/TSB, zone distribution, pace/power
  curves, recovery trends from HRV/sleep/Body Battery.
- **Phase 3 — Agents:** planner, evaluator and adjuster agents on Claude, with data exposed as
  tools / an MCP server over the normalized store. Plans stored as versioned documents.
- **Phase 4 — Delivery:** UI, plan calendar, FIT workout file export to the watch.
