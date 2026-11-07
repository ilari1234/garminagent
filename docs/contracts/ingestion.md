# Ingestion contract

Implemented in M2–M5, extended in M6a. Code: `src/garminagent/garmin/`, `sync.py`, `runner.py`,
`normalize.py`, `renormalize.py`, `fit.py`, `cli.py`.

## Garmin client (`garminagent.garmin`)

- `GarminClient.from_settings(settings)`, used as a context manager. Methods return **raw JSON**;
  typing happens in `normalize.py`, so schema drift can't break fetching.
- Range methods check their span against `RANGE_LIMITS` before sending. Callers split with
  `date_chunks`. Limits in days: daily summaries 365; sleep, hydration, Body Battery 31; steps,
  stress, HRV, weight, weight goals, wellness activities 28. Activity pages hold 99.
- Pacing is 1 request per second with bounded backoff on 429, 5xx and network errors.
- Errors: `GarminAuthError`, `GarminRateLimitError`, `GarminApiError`, `UnexpectedFileError`, all under
  `GarminError`. **Messages carry method, path and status only, never bodies or headers.**
- The token is never persisted. Only this package talks to Garmin.

## Normalization (`garminagent.normalize`)

`parse_activities`, `parse_daily_summaries`, `parse_sleep`, `parse_hrv`, `parse_weight`, `parse_hr_zones`
each take a raw payload and return `Normalized[T]` with `records` and a `skipped` count.

- Lenient: unknown or malformed items are skipped and counted; they never raise for the run.
- Must not emit GPS or free text. HRV status passes only if it's an enum-shaped code; HR zone `sport`
  and training method only if they are the spec's codes (an unknown sport skips the item, an unknown
  method becomes NULL).
- Thresholds share one range table, `THRESHOLD_RANGES` (bpm, W, m/s), used for activity fields, HR
  zones and manual input alike. `getlactateThresholdSpeed` is converted from the spec's cm/ms to m/s
  (×10); the owner's account never sends this field, so the unit is unconfirmed (ADR 0013).
- `renormalize(sessions, athlete, dataset)` re-parses the newest stored version of each record for
  `activities`, `daily_summaries`, `sleep`, `hrv` or `weight` into its table: no Garmin requests,
  inserts and updates only.

## FIT parsing (`garminagent.fit`)

`parse_fit(bytes) -> ParsedFit` with `samples` and `laps`; raises `FitParseError` whose `reason` is a
fixed code (`too_large`, `not_fit`, `integrity`, `decode`, `no_records`, `too_many_records`). The only module that may import `garmin_fit_sdk` (ADR 0004). `PARSER_VERSION` is 1; raising it
re-parses stored files. Caps: 64 MiB, 400,000 records. Out-of-range values become `None`.

## Sync (`garminagent.sync`, `garminagent.runner`)

`run_sync_once(settings, since=None, max_requests=None) -> RunOutcome(stopped, report, exit_code)` is
the single entry point for one locked, recorded run.

| Dataset (`sync_cursor.dataset`) | Source | Target table |
|---|---|---|
| `daily_summaries` | daily summaries v2 | `daily_summary` |
| `sleep` | sleep range | `sleep_summary` |
| `hrv` | HRV range | `hrv_summary` |
| `weight` | weight range | `weight_daily` |
| `activities` | activity search, 90-day chunks | `activity` |
| `hr_zones` | HR zones v2, `generateDefaults=false`, at most once per day (cursor end = last fetch day), first in the run | `athlete_zones` (a row only when a value changes) |

Report-only keys in `counts`: `activity_files`, `fit_files`, `analytics`. All counts are integers.

**Adding a dataset** means: a client method, a `parse_*` returning `Normalized`, a table without GPS
or free text, an upsert, a `RangeDataset` or its own step, tests against the fake server and a schema
guard entry. A 4xx skips only that dataset for the run.

**Activity file downloads** (ADR 0015): only 404 and 410 mark a file `unavailable`. 401, 403 and 5xx
stop the run and leave the file untried; other 4xx leave it untried and count as `errors`. A
download that can't be used (not a zip or FIT, no FIT member, a zip that can't be read) is marked
`invalid`. An unexpected exception in the Garmin part of the run stops it as `internal_error` and
FIT parsing still runs; one in FIT parsing keeps the run's counts and marks it `internal_error`
unless it had already stopped for another failure. One raised before the sync starts (building the
client) is recorded as an `internal_error` run with no requests or counts. Each ends with exit code
1 and shows in `status`.

**Post-sync steps** run inside the lock and make no Garmin requests. Bad input must not fail the run:
`parse_pending_fit_files` marks a bad file `invalid` and continues. Only one FIT file per activity is
used (the first by name); others are marked `skipped`. `garminagent.analytics.run_analytics` follows
(M6a): it runs after any stop, never raises, and its counts go to `counts["analytics"]`, with
`errors` for a failed part (logged by class name). It reads the normalized layer only.

## Blob store (`garminagent.storage.blobs`)

```python
class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...  # missing key raises FileNotFoundError
    def exists(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> int: ...
```

Keys: `{athlete_id}/activities/{activity_id}/{kind}-{sha256}.{ext}`, always passed through
`validate_key`. Never built from Garmin-supplied names. `make_blob_store(settings)` picks the backend
(`BLOB_BACKEND`): `LocalBlobStore` under `DATA_DIR/blobs`, or `S3BlobStore` (`storage/s3.py`, B3b) in
`BLOB_S3_BUCKET`. Both pass one contract suite (`tests/storage/test_blobs.py`). `delete_prefix(p)`
removes the key `p` and every key under `p/`, never a sibling such as `p0/`. Other backend failures
raise `BlobStoreError`, an `OSError` carrying the operation, key and status only.

## CLI

Every command prints JSON on stdout and logs on stderr, with no health values in logs.

| Command | Purpose |
|---|---|
| `version`, `config`, `db-check`, `auth-check` | Diagnostics. `config` hides secrets. `auth-check` makes two Garmin requests |
| `db-upgrade` | Migrations; uses `MIGRATION_DATABASE_URL` |
| `db-bootstrap` | Creates the owner and app roles where no init script runs (RDS), with `DB_MASTER_USERNAME`/`DB_MASTER_PASSWORD`; a re-run only sets passwords. Prints each role's outcome; exit 2 if refused, 1 on a database error |
| `sync [--since DATE] [--max-requests N]` | One run |
| `schedule [--interval-hours H]` | Local loop around the same run |
| `status [--max-age-hours H]` | Counts, dates and stop reasons. Read-only; exits 1 if unhealthy with a max age |
| `parse-fit` | Parse pending FIT files; no Garmin requests |
| `renormalize DATASET` | Re-parse stored responses into a normalized table; no Garmin requests. Holds the sync lock (`already_running` if busy) |
| `analyze` | The analytics step alone; prints counts and unmapped sport keys. Holds the sync lock; exit 1 if a part failed |
| `thresholds set TYPE VALUE [--sport] [--from]`, `unset TYPE --from [--sport]`, `list [--at] [--sport]` | Manual thresholds; out-of-range input exits 2. `list` prints values to stdout only |
| `purge-data --yes` | Delete all rows, then blobs. Without `--yes`, exits 2 |

| Exit code | Meaning |
|---|---|
| 0 | Complete, `budget` or `already_running` |
| 75 | `rate_limited`: try later |
| 1 | `garmin_error`, `auth_failed`, `database_error`, `internal_error` |

`stopped` values: `null`, `budget`, `rate_limited`, `garmin_error`, `auth_failed`, `database_error`,
`internal_error`, `already_running`. Monitoring and the AWS alarms depend on these names; `already_running` isn't
recorded in `sync_run`.

## Configuration

Environment only (`.env` locally), read by `Settings`. See `src/garminagent/config.py` and
`.env.example`: `GARMIN_PERSONAL_API_KEY`, `GARMIN_API_KEY_EXPIRES`, `GARMIN_BASE_URL`,
`GARMIN_MIN_REQUEST_INTERVAL`, `GARMIN_MAX_RETRIES`, `DATABASE_URL`, `MIGRATION_DATABASE_URL`,
`DB_APP_ROLE`, `SYNC_START_DATE` (default two years back), `SYNC_MAX_REQUESTS` (300, minimum 10),
`SYNC_REFETCH_DAYS` (3), `SYNC_ACTIVITY_REFETCH_DAYS` (7), `SCHEDULE_INTERVAL_HOURS` (6),
`SCHEDULE_CATCH_UP_MINUTES` (15), `DATA_DIR`, `LOG_LEVEL`, `LOG_FORMAT`. Secrets are `SecretStr`.
