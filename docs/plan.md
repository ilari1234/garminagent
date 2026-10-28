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
| AI model access (phase 3) | Claude on Amazon Bedrock in an EU region, data retention mode `none` — see [Data handling](#data-handling) |
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
  with `Authorization: ApiKey <key>` → access token (plain-text JWT) valid 24 h. No refresh
  token; request a new one.
- **Shape:** 26 read-only GET endpoints, pull only (no webhooks).
- **Covered:** activity search/summary/splits/workouts, original activity file download, HR zones,
  HRV, sleep, daily summaries (≤1 year per call), stress, steps, Body Battery, SpO2, daily HR,
  weight, hydration, nutrition, fitness age, user settings. Activity summaries include training
  load, aerobic/anaerobic TE, NP, IF, FTP, lactate threshold and VO2max values.
- **Not covered:** Training Status/Readiness, acute/chronic load, race predictor, VO2max trend,
  and any write access (no workout/calendar push). → We compute load/fitness metrics ourselves;
  plans live in our app (later: FIT workout file export).
- **Measured in M0** ([evaluation](garmin-personal-api/evaluation.md)): all endpoints work,
  no rate-limit headers and no throttling at 1 req/s, activities back to 2019, activity files
  delivered as zip, range limits partly enforced (stress 28 d strictly).

## Data handling

The data is personal health data (GDPR special category). It already lives at Garmin and
the services synced from it; this app adds as few parties and as little exposure as possible.
Policy facts below were checked against Anthropic and AWS documentation on 2026-10-25.

### Model access

- **Production agents use Claude on Amazon Bedrock**, in an EU region (`eu-north-1` or
  `eu-central-1`). On Bedrock, AWS is the processor; Anthropic has no access to prompts or
  completions, and nothing is used for training.
- **Retention mode `none`**, set per region and enforced with an SCP/IAM condition on
  `DataRetentionMode`, so it can't be loosened by accident. Models that require retention
  (currently Claude Fable 5/5.1, which require `aws_review`) are therefore not used, unless
  that is consciously decided later.
- **No cross-region inference outside the EU**: retained data would be stored in the region
  that processes the request.
- The direct Claude API is an acceptable fallback (commercial terms: no training; content not
  retained by default except for Covered Models with 30-day retention). Consumer Claude plans
  are never used for the app's data.

### What may reach a model

- **Agents see data only through tools** over the normalized store, never raw tables or files.
- **Default: derived metrics** — weekly load, CTL/ATL/TSB, time in zones, HRV/sleep/resting HR
  trends, per-session summaries (date, sport, duration, distance, load, TE, avg HR/pace/power).
- **Never sent:** GPS coordinates and tracks (reveal home/work), activity names and
  descriptions (free text), profile data, Garmin IDs, any credentials.
- **Off unless explicitly enabled in config:** weight/body composition, nutrition, hydration,
  menstrual-cycle data.
- **Every tool call is logged** (tool, arguments, returned payload size and fields, timestamp)
  so it is auditable what left the system.
- **Garmin-sourced text is data, never instructions** (prompt-injection hygiene).

### Storage and secrets

- Raw and normalized data stay in the deployment region: RDS/S3 encrypted at rest with KMS,
  TLS in transit, local volumes only on the developer's machine.
- Garmin API key and cloud credentials live in Secrets Manager (env/.env locally), are never
  logged and never placed in prompts. Short-lived Garmin keys; delete a key to revoke it and
  all its tokens.
- Personal data is never committed: `.env` and `data/` are gitignored and excluded from the
  Docker build context; Claude Code is denied reads of `.env*` in `.claude/settings.json`.

### Development

- Tests and CI use fake fixtures shaped like real responses; no personal data in the repo.
- AI-assisted development works from structure-only reports, not raw payloads. Developers
  using a consumer Claude plan should check its model-training setting.

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
  429/5xx honoring `Retry-After`. Returns raw JSON so the raw store keeps payloads verbatim;
  typed (lenient, all-optional) models live in the normalization layer (M3), so schema drift
  never breaks ingestion.
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
- **Tooling:** ruff, pyright (strict), pytest (httpx `MockTransport` fakes; Postgres tests via
  the CI service / compose), GitHub Actions (lint, test, dependency audit, build image),
  multi-stage Dockerfile, docker-compose (app + postgres).
- **Supply chain:** actions pinned to commit SHAs, base images pinned by digest, uv version
  pinned once (`required-version` in pyproject.toml, read by CI and enforced by uv); dependencies locked with hashes (`uv.lock`, installed `--locked`); Dependabot
  weekly for actions, uv, Dockerfile and compose with a 7-day cooldown; `pip-audit` over the
  exported lock gates the image build and runs weekly; zizmor and a Trivy image scan gate the
  image too; ruff bandit rules and a suite-wide credential-leak test run on every push. Details
  and the threat model: [SECURITY.md](../SECURITY.md).

## Security in every milestone

Security is part of each milestone's definition of done, not a later phase:

- **Every milestone** ends with the checklist in [SECURITY.md](../SECURITY.md#security-in-every-milestone),
  `/security-review` on the change, and SECURITY.md updated if the threat model changed.
- **Deeper reviews** at the risky transitions, marked 🔒 below: M3 (storage), phase 3
  (agents), and the AWS deployment.
- **Rules for AI-assisted work** live in [CLAUDE.md](../CLAUDE.md), which every Claude Code
  session loads.
- **Routine maintenance** (owner): weekly — read audit results and Dependabot PRs; monthly —
  `uv lock --upgrade` (until Dependabot supports Python 3.15) and bump the Trivy image digest;
  every 90 days — new Garmin key, delete the old one, reread SECURITY.md. GitHub settings to
  enable: branch protection on `main` requiring CI, Dependabot alerts and security updates.

## Phase 1 milestones

- **M0 — Spike (done 2026-10-24):** `spikes/probe_personal_api.py` probed every endpoint;
  findings in [garmin-personal-api/evaluation.md](garmin-personal-api/evaluation.md).
- **M1 — Scaffold:** pyproject, src layout, ruff/pyright (strict)/pytest, settings + logging +
  CLI (`version`, `config`, `db-check`), GitHub Actions (lint, test with Postgres, image to
  GHCR on main), multi-stage Dockerfile, compose with Postgres 18.
- **M2 — Client:** `garminagent.garmin` — plain-text JWT token handling (expiry from `exp`,
  renewal before expiry and on 401), all 26 endpoints returning raw JSON, per-endpoint range
  limits plus `date_chunks`, activity paging, activity zip → validated FIT files, pacing
  (1 req/s), backoff on 429/5xx/network errors with a hard stop on long `Retry-After`;
  `garminagent auth-check`; tests against a fake server with synthetic fixtures.
- **M3 — Storage 🔒:** `garminagent.storage` — schema (raw payloads with content-hash
  versioning, activity files, sync cursors, normalized `activity` and `daily_summary`), Alembic
  migrations run by `garminagent db-upgrade`, a `BlobStore` with a local implementation,
  repository upserts, `purge-data`; `garminagent.normalize` for activities and daily summaries;
  least-privilege app role in compose. Review results in [SECURITY.md](../SECURITY.md#m3-storage-review-2026-10-25).
- **M4a — Sync engine:** `garminagent.sync` and `garminagent sync` — daily summaries,
  activities (paged, progress saved per page) and activity files; contiguous synced ranges per
  dataset (backfill extends backwards, incremental re-fetches recent days); per-run request
  budget; clean stop on rate limiting; file outcomes (stored / unavailable / invalid) so missing
  files aren't retried; migration 0002.
- **M4b — FIT parsing:** `garminagent.fit` on Garmin's official `garmin-fit-sdk` —
  per-second `activity_sample` rows (HR, cadence, power, speed, distance, altitude,
  temperature; no GPS) and `activity_lap`; parse status and parser version per file, so a
  newer parser re-parses; runs at the end of every sync (no Garmin requests) and as
  `garminagent parse-fit`; migration 0003. License constraints in SECURITY.md.
- **M4c — Wellness data:** `sleep_summary` (durations by stage, score, sleep HR, stress,
  respiration, SpO2, timing; no free text), `hrv_summary` (nightly, weekly, baseline bands,
  status) and `weight_daily` (latest weigh-in, BMI, body fat), each synced as a date-range
  dataset; stress-duration and end-of-day Body Battery columns on `daily_summary` (already in
  the daily summaries, so no extra endpoints or requests); a 4xx from one dataset skips only
  that dataset for the run; minimum sync budget of 10; migration 0004.
- **M5 — Run it:** scheduled containerized sync, operational docs.

## Later phases (outline)

- **Phase 2 — Analytics:** load (TRIMP/TSS-like), CTL/ATL/TSB, zone distribution, pace/power
  curves, recovery trends from HRV/sleep/Body Battery.
- **Phase 3 — Agents 🔒:** planner, evaluator and adjuster agents on Claude via Bedrock, with
  data exposed as tools / an MCP server over the normalized store, under the
  [data-handling](#data-handling) rules. Plans stored as versioned documents. Review: the
  OWASP LLM/Agentic mapping in SECURITY.md, safety limits as code, tool permissions,
  prompt-injection tests.
- **Phase 4 — Delivery:** UI, plan calendar, FIT workout file export to the watch.
- **AWS deployment 🔒** (when it happens): least-privilege IAM, Secrets Manager, Bedrock
  retention SCP, EU regions only, CloudTrail, KMS, budget alarms.
