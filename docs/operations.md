# Operations

How to run garminagent day to day: first setup, the scheduled sync, monitoring, maintenance,
backups and troubleshooting. Security rules and routines are in [SECURITY.md](../SECURITY.md);
this guide links to them rather than repeating them.

All commands run from the repository root. `docker compose run --rm app <command>` runs a
one-off command; every command prints its result as JSON on stdout and logs on stderr.

## First setup

1. **Garmin key.** In Garmin Connect web, open the Personal API Key page, create a key (90-day
   expiry recommended) and copy it once.
2. **`.env`** (gitignored, never committed): copy `.env.example` and set
   - `GARMIN_PERSONAL_API_KEY`, and `GARMIN_API_KEY_EXPIRES` (`YYYY-MM-DD`) for expiry warnings
   - `POSTGRES_PASSWORD` and `APP_DB_PASSWORD` — your own values, e.g. `openssl rand -hex 24`
   - optionally `SYNC_START_DATE` to backfill further than two years.
3. **Build and start:**

   ```bash
   docker compose build
   docker compose up -d
   ```

   This starts `db` (Postgres 18, localhost only), runs `migrate` once (schema as the owner
   role) and starts `scheduler`, which syncs immediately.
4. **Check the key** (optional, two Garmin requests):

   ```bash
   docker compose run --rm app auth-check
   ```

The database volume is initialized only once. If you change the passwords in `.env` later,
recreate it with `docker compose down -v` — **this deletes all stored data** (it can be synced
again from Garmin).

## The scheduled sync

`scheduler` runs `garminagent schedule`:

| Last run ended | Next run |
|---|---|
| complete | after `SCHEDULE_INTERVAL_HOURS` (6) |
| at the request budget (backfill in progress) | after `SCHEDULE_CATCH_UP_MINUTES` (15) |
| rate limited by Garmin | after the interval, and at least 2 hours |
| Garmin or database error | 30 min, doubling up to the interval |
| authentication failed | after the interval (fix the key, see below) |

Each run makes at most `SYNC_MAX_REQUESTS` (300) Garmin requests at one per second, stores
everything it fetched, and parses new FIT files. A first two-year backfill takes a few runs,
mostly downloading activity files; the scheduler continues automatically every 15 minutes
until it is complete.

Only one sync runs at a time (a Postgres advisory lock): a manual `sync` while the scheduler
is busy skips with `"stopped": "already_running"`. Stopping or restarting the scheduler is
safe at any point — an interrupted run continues where it stopped.

```bash
docker compose stop scheduler     # pause
docker compose start scheduler    # resume
docker compose run --rm app sync  # one run now, outside the schedule
```

## Monitoring

```bash
docker compose run --rm app status
docker compose ps                  # scheduler health: healthy / unhealthy
docker compose logs -f scheduler   # progress lines: counts and date ranges only
```

`status` shows the last run (when, complete or why it stopped, requests used), the synced
date range per dataset and pending activity / FIT files. The scheduler's health check runs
`status --max-age-hours 13`: it turns **unhealthy** if no run has finished in 13 hours (two
missed intervals) or the last run failed (`auth_failed`, `garmin_error`, `database_error`,
`internal_error`).

| `stopped` | Meaning | Action |
|---|---|---|
| `null` | complete | — |
| `budget` | more to fetch; continues in 15 min | — |
| `already_running` | another sync was busy | — |
| `rate_limited` | Garmin asked us to slow down | none; retries after ≥ 2 h. If it persists for days, lower `SYNC_MAX_REQUESTS` |
| `garmin_error` | Garmin 5xx or an unexpected response | usually transient; check logs if it repeats |
| `auth_failed` | key missing, expired or deleted | rotate the key (below) |
| `database_error` | Postgres unreachable or misconfigured | `docker compose ps db`, check passwords in `.env` |
| `internal_error` | an unexpected error (logs name its class) | the scheduler backs off and retries; if it repeats, check the logs and report it |

A dataset that answers 4xx (e.g. a feature your account doesn't have) is skipped for that run
and shows up as `"errors"` in its counts; the other datasets continue. Activity file downloads
are marked unavailable only on 404 or 410; a 401 or 403 stops the run instead, so an auth glitch
at Garmin can't write files off.

### Real-data check

`scripts/real_data_check.py` reports what sync stored as counts, byte sizes, day counts and
Garmin's key names: runs and synced ranges, file and parse outcomes, rows per table, which unit
weigh-ins arrive in, the size of `activity_sample`, which keys the raw sleep payloads carry, how far
apart samples are, laps, HR and power coverage per sport, and threshold coverage (which threshold
fields the activities carry, which unit or scale their values fit, the HR zone history and the
threshold timeline rows per source). It prints no measured values, dates or
IDs, so its output can be shared with a development session. Read-only, no Garmin requests. The
script isn't in the image; mount it into the app container (use the repository's absolute path):

```bash
docker compose run --rm --no-deps --entrypoint python -v "$PWD/scripts:/scripts:ro" app /scripts/real_data_check.py
```

## Key rotation (every 90 days)

1. Garmin Connect web → Personal API Key → create a new key.
2. Update `GARMIN_PERSONAL_API_KEY` and `GARMIN_API_KEY_EXPIRES` in `.env`.
3. `docker compose up -d scheduler` (recreates it with the new environment).
4. `docker compose run --rm app auth-check`, then delete the old key in Garmin Connect.

The app warns in its logs from 14 days before the configured expiry. The other 90-day items
are in SECURITY.md (routine maintenance).

## Upgrading

```bash
git pull
docker compose build
docker compose up -d     # runs migrate again (no-op if nothing new), restarts scheduler
```

If a release improves FIT parsing (`PARSER_VERSION`), already stored files are re-parsed on
the next sync, or right away with `docker compose run --rm app parse-fit`.

If a release reads new fields from responses already stored (M6a: the threshold columns on
`activity`), re-parse them once; no Garmin requests, no API key needed:

```bash
docker compose run --rm app renormalize activities   # also: daily_summaries, sleep, hrv, weight
docker compose run --rm app analyze                  # rebuild the threshold timeline from them
```

`renormalize` uses the newest stored version of each record and prints counts only. Like
`analyze`, it waits for no one: if a sync holds the lock it prints `"stopped": "already_running"`;
run it again afterwards.

## Analytics and thresholds

Every sync ends with the `analyze` step (phase 2, ADR 0013); `garminagent analyze` runs it alone.
In M6a it groups activities by sport and rebuilds the threshold timeline. It never fails a sync:
its counts are in `sync_run.counts["analytics"]`, and a failure shows as `"errors"` there and as a
log line naming the exception class. `analyze` prints `unmapped_sport_keys`: Garmin activity types
not yet in a sport group (they count as `other`; add them in `garminagent/analytics/sports.py`).

HR zones are fetched once a day (`hr_zones` in `status`) and kept as history. Thresholds come from
three sources; on any date the first one that has a value wins:

1. **manual** — yours, set with `thresholds set`. Holds until your next value or `thresholds unset`,
   even if Garmin's values change later.
2. **garmin_zones** — max, resting and lactate threshold HR from Garmin's HR zone settings, from the
   day they were first fetched.
3. **activity** — values Garmin sent with your activities: LTHR per sport group, FTP from rides,
   threshold speed from runs. These cover history before the first zones fetch.

```bash
docker compose run --rm app thresholds list --at 2026-11-01 --sport run
docker compose run --rm app thresholds set ftp 250 --sport ride --from 2026-11-01
docker compose run --rm app thresholds set lthr 168            # all sports, from today (UTC)
docker compose run --rm app thresholds unset ftp --sport ride --from 2026-11-01
```

Units: bpm for `lthr`, `max_hr` and `resting_hr`; W for `ftp`; m/s for `threshold_speed`
(4:30 min/km is 3.70 m/s). Values outside a plausible range are refused with exit code 2:
LTHR 80–230, max HR 100–250, resting HR 25–120, FTP 30–700 W, threshold speed 0.5–10 m/s.
`thresholds list` prints values to your terminal only; logs never carry them. It is for
interactive use: don't run it where stdout is collected as logs (a scheduled container, CloudWatch).

## Backups and restore

Backups contain your health data. Keep them out of the repository (`data/` is gitignored)
and on an encrypted disk; delete old ones you don't need.

```bash
mkdir -p data/backups
docker compose exec -T db pg_dump -U garminagent -Fc garminagent > data/backups/db.dump
docker run --rm -v garminagent_appdata:/data:ro -v "$PWD/data/backups:/backup" alpine \
  tar -czf /backup/blobs.tgz -C /data blobs
```

Restore into a fresh stack (`docker compose down -v`, then `docker compose up -d db`):

```bash
docker compose exec -T db pg_restore -U garminagent -d garminagent --clean --if-exists < data/backups/db.dump
docker run --rm -v garminagent_appdata:/data -v "$PWD/data/backups:/backup" alpine \
  tar -xzf /backup/blobs.tgz -C /data
```

Everything can also be re-synced from Garmin (no backup needed for completeness), within
the history Garmin keeps.

## Deleting data

```bash
docker compose stop scheduler
docker compose run --rm app purge-data --yes
```

Deletes every stored row and file (the Garmin account is untouched). Restart the scheduler
only if you want syncing to start again. Delete backups separately.

## Moving to AWS

The same image and commands; only configuration and the scheduler change. The staged plan, cutover
and rollback are in [aws-deployment.md](aws-deployment.md); see SECURITY.md (AWS deployment review)
before going live.

| Local | AWS |
|---|---|
| `scheduler` service (`schedule` loop) | EventBridge Scheduler → ECS Fargate task running `sync` (one-shot) every few hours, plus a more frequent rule until the backfill is complete; the advisory lock prevents overlaps |
| `migrate` service | ECS task running `db-upgrade` as a deployment step |
| Postgres container | RDS PostgreSQL 18, `sslmode=verify-full`, KMS encryption, automated backups |
| `appdata` volume | `BlobStore` on S3 (to be implemented), SSE-KMS |
| `.env` | Secrets Manager → task environment |
| `docker compose logs` | CloudWatch Logs (JSON lines already) |
| health check | CloudWatch alarm on failed task exit codes (1, 75) or a scheduled `status --max-age-hours` |
| GHCR image | stays private (FIT SDK license); mirror to a private ECR repository |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `required variable POSTGRES_PASSWORD is missing a value` | passwords not in `.env` | set `POSTGRES_PASSWORD` and `APP_DB_PASSWORD` |
| `database_error`, OperationalError on login | `.env` passwords changed after the volume was created | recreate with `docker compose down -v` (deletes data) |
| Old behaviour after `git pull` | image not rebuilt | `docker compose build && docker compose up -d` |
| Sync stays at `budget` for days | very long history | normal while catching up; or raise `SYNC_MAX_REQUESTS` cautiously |
| Scheduler `unhealthy` | last run failed or none in 13 h | `docker compose run --rm app status`, then the table above |
| `activity file rejected (...)` / `FIT file rejected (...)` in logs | a file Garmin served can't be used | marked `invalid`, not retried; nothing to do |
