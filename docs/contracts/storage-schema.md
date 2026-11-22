# Storage schema contract

Implemented, migrations 0001–0008. **`src/garminagent/storage/models.py` is authoritative** for
columns and types; this file says what they mean and who may rely on what. Reasoning: ADR 0003.

## Layers and who may read them

| Layer | Tables | Readers | Writers |
|---|---|---|---|
| Raw | `raw_payload`, `activity_file`, blobs | `normalize`, `fit` and `renormalize` only, plus `purge-data` | Sync |
| Operational | `athlete`, `sync_cursor`, `sync_run` | Sync, `status` | Sync, runner |
| Normalized | `activity`, `daily_summary`, `activity_sample`, `activity_lap`, `sleep_summary`, `hrv_summary`, `weight_daily`, `athlete_zones` | Analytics | Sync, FIT parser, `renormalize` |
| Derived | `athlete_threshold` (M6a), `activity_metrics` (M6b), `daily_load` and the view `daily_load_weekly` (M6c), `activity_best_effort` and the views `best_effort_curve` and `best_effort_pb` (M6d); more follow in M6e (ADR 0013) | Analytics; agent layer through views (M6f) | Analytics; `manual` thresholds by `garminagent thresholds` |

**Agents read none of these directly** ([agent layer](agent-layer.md)).

## Invariants

1. **Normalized and derived tables hold no GPS and no free text** (names, descriptions, notes).
   Enforced by `tests/storage/test_schema.py`, which also fails on a table not assigned to a layer.
   Text columns in derived tables hold codes, held by CHECK constraints.
2. **Every data table has `athlete_id`** with `ON DELETE CASCADE`. Single-user today.
3. **Normalized rows are replaceable.** Anything can be rebuilt from raw. Nothing unique to the owner
   lives here. Derived tables should keep this property, apart from manual inputs such as thresholds.
4. **Upserts always set `updated_at`**, even when values are unchanged, so it means "last written", not
   "last changed". Recent days and activities are rewritten on every run. Two derived tables
   differ: in `activity_metrics` and `activity_best_effort` it means "last computed" (fingerprint
   changed), in `daily_load` "last changed" (only differing rows are written).
5. **`raw_payload_id` is optional and `SET NULL`** on delete; provenance, not a dependency.
6. **Missing means NULL.** Values out of plausible range become NULL at parsing, in every dataset
   (activities and daily summaries since ADR 0015; older rows keep their values until re-normalized).
   Almost all measures are nullable, so consumers must handle NULL.
7. **Roles:** the app role has DML only. A new table's migration must grant it explicitly (from 0006;
   0002–0005 relied on 0001's default privileges), and test it in `tests/storage/test_privileges.py`.
   A view gets SELECT only: 0001's default privileges also cover views, so its migration revokes
   them first (0008). Rollup views in `public` (`daily_load_weekly`, `best_effort_curve`,
   `best_effort_pb`) are `security_invoker`, so
   they never show more than the reader's own grants on the tables beneath; they are for roles
   that already read those tables. The `agent` schema's views and their privileges are M6f's to
   decide (the agent role reads no `public` table, [agent layer](agent-layer.md)).
   Roles come from `docker/postgres/init` locally and from `db-bootstrap` on RDS, where the owner is
   not a superuser: it holds CONNECT and `USAGE, CREATE ON SCHEMA public WITH GRANT OPTION`, nothing
   more. Migrations must work with exactly that (no extensions, no `ALTER` of objects the owner
   didn't create); `tests/storage/test_bootstrap.py` runs them all that way.
8. **Migrations are linear, numbered and run as the owner.** Next: 0010. Only track A adds them.

## Tables

| Table | Primary key | Contents |
|---|---|---|
| `athlete` | `id` | One row |
| `raw_payload` | `id`; unique `(athlete_id, endpoint, request_key, sha256)` | One row per distinct response. Same content refreshes `last_fetched_at`; changed content adds a row. `payload` NULL means no content (204) |
| `activity_file` | `id`; unique `blob_key`, and `(athlete_id, activity_id, kind, sha256)` | `kind` is `original` or `fit`. `parse_status` NULL (unparsed), `parsed`, `invalid`, `skipped`, with `parser_version`. `member_name` is data, never a path |
| `sync_cursor` | `(athlete_id, dataset)` | Contiguous `synced_from`..`synced_through` |
| `sync_run` | `id` | `started_at`, `finished_at`, `stopped`, `requests`, `counts` (JSONB of integers only) |
| `activity` | `(athlete_id, activity_id)` | Start times (UTC, and local which may be NULL), `sport`, `event_type`, durations, distance, elevation, HR, power, Garmin's TE, load and VO2max, HR zone seconds, and since 0006 the thresholds Garmin reported with it (`lactate_threshold_hr`, `threshold_speed_mps`, `ftp_w`) plus Garmin's `garmin_tss` and `garmin_if`. `file_status` NULL (untried), `stored`, `unavailable`, `invalid`; only NULL is retried |
| `daily_summary` | `(athlete_id, calendar_date)` | Resting, min and max HR; steps; stress average, max and minutes by level; Body Battery min, max, charged, drained, end of day; respiration; intensity minutes; calories |
| `activity_sample` | `(athlete_id, activity_id, elapsed_s)` | `heart_rate`, `cadence`, `power_w`, `speed_mps`, `distance_m`, `altitude_m`, `temperature_c`. FK to `activity`, cascade |
| `activity_lap` | `(athlete_id, activity_id, lap_index)` | Start offset, elapsed and timer seconds, distance, HR, average power. FK to `activity`, cascade |
| `sleep_summary` | `(athlete_id, calendar_date)` | Start and end, seconds by stage, naps, awake count, score, average HR and SpO2 (always empty on real data: Garmin's sleep summaries send these keys as null), stress, respiration |
| `hrv_summary` | `(athlete_id, calendar_date)` | Last night average and 5-min high, weekly average, Garmin's baseline bands, `status` code. Milliseconds |
| `athlete_zones` | `id`; unique `(athlete_id, sport, valid_from)` | Garmin's HR zone settings per zone sport (`DEFAULT`, `RUNNING`, `CYCLING`, `SWIMMING`, `ROWING`): training method code, five zone floors, max, resting and LT HR used. A row from the day a change was first fetched; holds until the next row for that sport |
| `athlete_threshold` | `id`; unique `(athlete_id, value_type, sport, source, valid_from)` | The threshold timeline: `value_type` (`ftp`, `lthr`, `max_hr`, `resting_hr`, `threshold_speed`), `sport` (`all` or a sport group), `source` (`manual`, `garmin_zones`, `activity`), `valid_from`, `value` in bpm, W or m/s. `manual` rows are the owner's; the others are rebuilt by `analyze` |
| `activity_metrics` | `(athlete_id, activity_id)`, FK to `activity`, cascade; index `(athlete_id, activity_date)` | One row per activity (M6b): `activity_date` (local start day, else UTC), `sport_group`, `moving_s` (NULL: no samples), HR, power and speed coverage (fractions of moving time), weighted power, intensity, power load, HR load, the chosen `load` with `load_method` (`power`, `hr`, `none`), seconds in HR zones 0–5 (0 is below zone 1) and power levels 1–7, the 3-zone split, efficiency factor with `ef_basis`, decoupling, the thresholds used (FTP, LTHR, max and resting HR, resting HR's source, zone floors' sport and date), `thresholds_backfilled`, `algo_version`, `input_fingerprint`. Written by `analyze` only |
| `daily_load` | `(athlete_id, calendar_date, sport_group)` | One row per day and group (M6c): `sport_group` is `all` (the total) or a sport group that has an activity, every day from the first activity to the latest day with data. Summed `load`, `sessions`, `moving_s`, `activities_unloaded` (metrics row without load) and `activities_pending` (no metrics row yet), `thresholds_backfilled`, `fitness` (42-day), `fatigue` (7-day), `form` (previous day's fitness − fatigue), `ramp` (fitness change over 7 days, NULL for the first 7), `history_days`, `fitness_reliable` (126 days or more), `algo_version`. Written by `analyze` only |
| `daily_load_weekly` (view) | — | `daily_load` per ISO week (Monday start) and group: `days` (under 7 is a partial week), summed load, sessions, moving time and missing-load counts, `thresholds_backfilled` if any day had it, and fitness, fatigue, form, ramp and `fitness_reliable` as of the week's last stored day |
| `activity_best_effort` | `(athlete_id, activity_id, kind, span)`, FK to `activity`, cascade; index `(athlete_id, sport_group, kind, span)` | Best efforts per activity (M6d): `kind` `distance` (runs: 400 m to marathon; rides: 10–100 km) with `span` in whole metres and `seconds` the shortest time, or `power` (every group; 5 s to 5 h) with `span` in seconds and `power_w` the highest mean. Every span the group is looked for has a row; NULL value: not reached. `activity_date`, `sport_group`, `algo_version`, `input_fingerprint`. Written by `analyze` only |
| `best_effort_curve` (view) | — | Per group, kind and span, the best value with its `activity_id` and `activity_date`, for all time (`period_days` NULL) and the last 365, 90 and 42 days. Ties to the earlier date |
| `best_effort_pb` (view) | — | Per group, kind and span, the all-time best with its activity and date, and the best set on an earlier day (`previous_seconds`, `previous_power_w`, `previous_activity_id`, `previous_activity_date`; NULL for a first effort) |
| `weight_daily` | `(athlete_id, calendar_date)` | Latest weigh-in in kg (Garmin sends grams despite the spec; converted, confirmed on real data 2026-11-03), BMI, body fat, entries. Sensitive: opt-in before reaching a model |

## Semantics consumers get wrong

- **`elapsed_s` is wall-clock from the first record, with gaps and no pause flag.** A gap may be smart
  recording or auto-pause. Not every second has a row.
- **Re-parsing replaces** samples and laps for an activity (delete, then insert).
- **`calendar_date` for sleep is the date Garmin assigns the night**, not necessarily the start date.
- **`normalized_power_w`, `training_load`, TE and VO2max on `activity` are Garmin's values**, kept for
  reference and cross-checks. `training_load` is on a different scale from the proposed loads.
- **Thresholds are resolved, not read.** Use `garminagent.analytics.thresholds` (`thresholds_at`,
  `load_timeline`): per type the first source in precedence manual → Garmin zones → activity with
  a value on or before the date wins, the group's own row before `all`, and a value holds until the
  next change. Then resting HR from the 7 daily summaries before the date (at least 3), then the
  type's earliest row carried back (`backfilled`). Reading one source's rows directly gives the
  wrong answer.
- **`activity_metrics.updated_at` is when the row was last computed**, unlike the normalized
  tables: rows are rewritten only when `input_fingerprint` changes. A NULL metric means "not
  computable" (no samples, coverage under 50 %, no threshold, `ANALYTICS_TRIMP_B` unset), never 0.
  `load` is NULL when `load_method` is `none`; rows with `thresholds_backfilled` used values from
  later dates and are approximate. Decoupling isn't limited to steady sessions (ADR 0013).
- **`daily_load` rows are rebuilt every run but written only when they change**, so its
  `updated_at` means "last changed". Load 0 doesn't always mean rest: check `activities_unloaded`
  and `activities_pending`. Fitness starts from 0 on the first activity's day, so before
  `fitness_reliable` fitness is too low, form too high and ramp too large; a new first activity
  (an older one synced) shifts the whole series.
- **`activity_best_effort` has a row for every span the group is looked for**, with the value
  NULL when the activity didn't reach it: NULL means "not reached", never 0. Distance efforts are
  only as good as the watch's distance (spikes over 12.5 m/s running or 30 m/s riding break the
  track; slower GPS errors aren't caught). The curve view's periods count back from
  `CURRENT_DATE` in the reader's time zone.
- **`athlete_zones.valid_from` is when we first saw a change**, not when it was made in Garmin, and
  history starts with the first fetch (M6a). Earlier dates fall back to activity values.
- **`garmin_tss` and `garmin_if` are Garmin's numbers** for cross-checks only; our loads use neutral
  names and our own definitions (ADR 0013).
- **Raw revisions accumulate.** The latest for a request is the highest `last_fetched_at`.
- **Dataset names and blob key layout** are listed in the [ingestion contract](ingestion.md).

## Local and AWS

Local: Postgres 18 in compose, blobs in the `appdata` volume. AWS (accepted, ADR 0008; not built): RDS with
`sslmode=verify-full`, S3 with SSE-KMS and no versioning. Same schema and code paths.
