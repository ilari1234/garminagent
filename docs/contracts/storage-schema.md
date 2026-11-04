# Storage schema contract

Implemented, migrations 0001–0005. **`src/garminagent/storage/models.py` is authoritative** for
columns and types; this file says what they mean and who may rely on what. Reasoning: ADR 0003.

## Layers and who may read them

| Layer | Tables | Readers | Writers |
|---|---|---|---|
| Raw | `raw_payload`, `activity_file`, blobs | `normalize` and `fit` only, plus `purge-data` | Sync |
| Operational | `athlete`, `sync_cursor`, `sync_run` | Sync, `status` | Sync, runner |
| Normalized | `activity`, `daily_summary`, `activity_sample`, `activity_lap`, `sleep_summary`, `hrv_summary`, `weight_daily` | Analytics | Sync, FIT parser |
| Derived | Phase 2, not built (ADR 0013) | Agent layer | Analytics |

**Agents read none of these directly** ([agent layer](agent-layer.md)).

## Invariants

1. **Normalized tables hold no GPS and no free text** (names, descriptions, notes). Enforced by
   `tests/storage/test_schema.py`. New tables must be covered.
2. **Every data table has `athlete_id`** with `ON DELETE CASCADE`. Single-user today.
3. **Normalized rows are replaceable.** Anything can be rebuilt from raw. Nothing unique to the owner
   lives here. Derived tables should keep this property, apart from manual inputs such as thresholds.
4. **Upserts always set `updated_at`**, even when values are unchanged, so it means "last written", not
   "last changed". Recent days and activities are rewritten on every run.
5. **`raw_payload_id` is optional and `SET NULL`** on delete; provenance, not a dependency.
6. **Missing means NULL.** Values out of plausible range become NULL at parsing, in every dataset
   (activities and daily summaries since ADR 0015; older rows keep their values until re-normalized).
   Almost all measures are nullable, so consumers must handle NULL.
7. **Roles:** the app role has DML only. A new table's migration must grant it, and test it in
   `tests/storage/test_privileges.py`.
8. **Migrations are linear, numbered and run as the owner.** Next: 0006. Only track A adds them.

## Tables

| Table | Primary key | Contents |
|---|---|---|
| `athlete` | `id` | One row |
| `raw_payload` | `id`; unique `(athlete_id, endpoint, request_key, sha256)` | One row per distinct response. Same content refreshes `last_fetched_at`; changed content adds a row. `payload` NULL means no content (204) |
| `activity_file` | `id`; unique `blob_key`, and `(athlete_id, activity_id, kind, sha256)` | `kind` is `original` or `fit`. `parse_status` NULL (unparsed), `parsed`, `invalid`, `skipped`, with `parser_version`. `member_name` is data, never a path |
| `sync_cursor` | `(athlete_id, dataset)` | Contiguous `synced_from`..`synced_through` |
| `sync_run` | `id` | `started_at`, `finished_at`, `stopped`, `requests`, `counts` (JSONB of integers only) |
| `activity` | `(athlete_id, activity_id)` | Start times (UTC, and local which may be NULL), `sport`, `event_type`, durations, distance, elevation, HR, power, Garmin's TE, load and VO2max, HR zone seconds. `file_status` NULL (untried), `stored`, `unavailable`, `invalid`; only NULL is retried |
| `daily_summary` | `(athlete_id, calendar_date)` | Resting, min and max HR; steps; stress average, max and minutes by level; Body Battery min, max, charged, drained, end of day; respiration; intensity minutes; calories |
| `activity_sample` | `(athlete_id, activity_id, elapsed_s)` | `heart_rate`, `cadence`, `power_w`, `speed_mps`, `distance_m`, `altitude_m`, `temperature_c`. FK to `activity`, cascade |
| `activity_lap` | `(athlete_id, activity_id, lap_index)` | Start offset, elapsed and timer seconds, distance, HR, average power. FK to `activity`, cascade |
| `sleep_summary` | `(athlete_id, calendar_date)` | Start and end, seconds by stage, naps, awake count, score, average HR and SpO2 (always empty on real data: Garmin's sleep summaries send these keys as null), stress, respiration |
| `hrv_summary` | `(athlete_id, calendar_date)` | Last night average and 5-min high, weekly average, Garmin's baseline bands, `status` code. Milliseconds |
| `weight_daily` | `(athlete_id, calendar_date)` | Latest weigh-in in kg (Garmin sends grams despite the spec; converted, confirmed on real data 2026-11-03), BMI, body fat, entries. Sensitive: opt-in before reaching a model |

## Semantics consumers get wrong

- **`elapsed_s` is wall-clock from the first record, with gaps and no pause flag.** A gap may be smart
  recording or auto-pause. Not every second has a row.
- **Re-parsing replaces** samples and laps for an activity (delete, then insert).
- **`calendar_date` for sleep is the date Garmin assigns the night**, not necessarily the start date.
- **`normalized_power_w`, `training_load`, TE and VO2max on `activity` are Garmin's values**, kept for
  reference and cross-checks. `training_load` is on a different scale from the proposed loads.
- **Raw revisions accumulate.** The latest for a request is the highest `last_fetched_at`.
- **Dataset names and blob key layout** are listed in the [ingestion contract](ingestion.md).

## Local and AWS

Local: Postgres 18 in compose, blobs in the `appdata` volume. AWS (accepted, ADR 0008; not built): RDS with
`sslmode=verify-full`, S3 with SSE-KMS and no versioning. Same schema and code paths.
