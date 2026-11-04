# Phase 1 review (M0–M5)

Independent review under ADR 0012, done 2026-11-04 by a fresh Claude Code session on branch
`docs/phase1-review`. It compares `main` at `fb8f7fa` with what the plan, contracts, ADRs and
SECURITY.md say it does.

## Verdict

**Phase 1 isn't "done" yet.** One high-severity finding is open: **H1**, where unexpected
exceptions escape the run and the scheduler loop. Everything the plan lists for M0–M5 is built,
the contracts mostly hold, and the security controls checked below are in place. The defects are
in failure paths, which is where tests are thinnest. Fixing H1, with tests, is enough to mark
the phase done. M1–M4 should be fixed before phase 2 relies on the data.

| Milestone | Verdict | Findings |
|---|---|---|
| M0 spike | Done | None. The spike withholds token headers and bodies (`spikes/probe_personal_api.py:78-80`) |
| M1 scaffold | Done | L7 (CI password literal), L9 (pacing has no lower bound) |
| M2 client | Built as planned. Some failures escape the error types | H1 (non-JSON body, corrupt zip), M1 (401 on downloads) |
| M3 storage 🔒 | Built. Two invariants don't hold | M2 (missing means NULL), M4 (superuser password in app env), L5 (grants), L6 (schema guard) |
| M4a sync | Built. Bad input can wedge it | H1, M1, M3 (duplicate keys), L10 (deletions) |
| M4b FIT | Built. Caps and guards hold | L1 (overflowing values wedge parsing), L4 (second FIT file wins) |
| M4c wellness | Built | M2 (daily summary values unchecked) |
| M5 run it | Built. Failure handling has gaps | H1 (scheduler exits), L2 (`database_error` not recorded), L3 (lock connection) |

## Scope and method

**Read:** `CLAUDE.md`, `PROJECT_STATE.md`, `docs/plan.md`, `docs/contracts/` (ingestion, storage
schema), `SECURITY.md`, ADRs 0001–0005 and 0009–0012, `PHASE1_RETRO.md`, `docs/operations.md`,
`OPEN_QUESTIONS.md` and the evaluation. Then all of `src/garminagent/`, the migrations, `tests/`,
`scripts/real_data_check.py`, the spike, `Dockerfile`, `docker-compose.yml`,
`docker/postgres/init/`, `.github/workflows/`, `dependabot.yml` and `.claude/settings.json`.

**Ran:** `uv sync --locked` with uv 0.13.6 through `uvx`, then `ruff check`, `ruff format
--check` and `pyright`, all clean. The full suite ran against a throwaway Postgres 18 container
on port 55432 with a password generated at runtime: 238 passed, 1 skipped (POSIX permissions on
Windows), 96 % line coverage. CI on `main` at `fb8f7fa` is green.

**Probes:** suspected defects were confirmed with scratch scripts outside the repo. They used
the suite's fakes (`tests/garmin/fakes.py`), synthetic data and fresh databases on the throwaway
server. Each finding below says which probe shows it. No Garmin calls were made, the owner's
compose database wasn't touched, and nothing under `data/` or `.env*` was read.

## Findings

Severity: **high** breaks a stated invariant in a way that needs manual recovery, or puts the
account or data at risk. **Medium** is a real defect with a plausible trigger. **Low** is an
edge case, a documentation mismatch or a test gap.

| ID | Sev | Finding | Milestone |
|---|---|---|---|
| H1 | High | Exceptions other than Garmin and database errors escape the run and kill the scheduler | M2, M4a, M5 |
| M1 | Medium | A 401 or 403 on downloads marks files `unavailable` for good, and the run reports complete | M4a |
| M2 | Medium | "Missing means NULL" isn't enforced for `activity` and `daily_summary` | M3, M4c |
| M3 | Medium | Duplicate keys in one payload abort every run at the same chunk | M4a |
| M4 | Medium | The scheduler and app containers get the Postgres superuser password | M3 |
| L1 | Low | Values too long or too large for their columns wedge sync or FIT parsing | M4a, M4b |
| L2 | Low | `database_error` is never recorded in `sync_run`, though the docs say it is | M5 |
| L3 | Low | The advisory-lock connection stays "idle in transaction" for the whole run | M5 |
| L4 | Low | After an interrupted parse, an activity's second FIT file replaces the first | M4b |
| L5 | Low | Migrations 0003–0005 don't grant their tables, contrary to invariant 7 | M3 |
| L6 | Low | The schema guard checks column names against a hand-kept table list | M3 |
| L7 | Low | Credential-shaped literal in CI | M1 |
| L8 | Low | Failure paths and CLI commands without tests | all |
| L9 | Low | Pacing and retry settings have no bounds | M1, M2 |
| L10 | Low | Activities deleted at Garmin are never removed | M4a |
| L11 | Low | Doc drift | docs |

### H1 — Exceptions other than Garmin and database errors escape the run and kill the scheduler

**Evidence.** `Syncer.run` catches `_BudgetExhausted` and `GarminError` subclasses only
(`sync.py:185-195`). `run_sync_once` catches only `SQLAlchemyError` (`runner.py:99`).
`run_schedule` catches nothing (`runner.py:134-145`). At least two ordinary inputs raise
something else:

- **A 200 response whose body isn't JSON**, such as a maintenance page or a proxy error page.
  `get_json` calls `resp.json()` unguarded (`garmin/client.py:85`). Probe A: the daily summaries
  endpoint answers 200 with an HTML body, and `Syncer.run` raises `json.JSONDecodeError`.
- **A zip that passes `is_zipfile` but whose member can't be read**: a bad CRC, an encrypted
  member or an unsupported compression method. `archive.read(m)` (`garmin/files.py:38`) raises
  `zipfile.BadZipFile`, `RuntimeError` or `NotImplementedError`, and only
  `UnexpectedFileError` is handled. Python's `zipfile` stops at the declared size, so a zip bomb
  that understates its size also ends here as `BadZipFile`. Probe B: two runs both raise
  `BadZipFile`, the file is downloaded twice, and `file_status` stays NULL.

**What follows.**

- No `sync_run` row is written, FIT parsing doesn't run (`sync.py:197`), and `sync` exits with a
  traceback.
- Under `schedule`, the exception leaves `run_schedule` and the process exits. Probe K confirms
  that `run_schedule` re-raises. Compose then restarts the scheduler (`restart: unless-stopped`,
  `docker-compose.yml:51`), which syncs at once, so the token, the refetch requests and the
  failing request repeat on every restart. Docker spaces restarts by up to about a minute, so this
  is far more traffic than the usual handful of requests every six hours. It keeps going until
  someone intervenes.
- For the zip case the loop never ends by itself. The activity stays NULL, files are taken
  newest first (`repository.py:199-208`), so the same file fails first in every run. No command
  can mark it `invalid` (OPEN_QUESTIONS 28), so recovery needs SQL by hand.
- This contradicts the ingestion contract ("Bad input must not fail the run",
  `docs/contracts/ingestion.md:51`), the retro's lesson that one bad file must not crash every
  run, and SECURITY.md's zip-bomb row ("fail loudly", `SECURITY.md:42`). The run fails every
  time, not once.

Typer 0.27.3 defaults to `pretty_exceptions_show_locals=False`, which was checked in the
installed package. So these tracebacks don't print local variables. The `Authorization` string
in `GarminTransport._send` would otherwise be one of them.

**Suggested fix.**

1. In `parse_activity_file`, turn `BadZipFile`, `RuntimeError`, `NotImplementedError`,
   `EOFError`, `zlib.error` and `OSError` from reading members into
   `UnexpectedFileError(..., "corrupt_zip")`.
2. In `get_json`, turn a `ValueError` from `resp.json()` into a `GarminError` that carries the
   method, path and status only.
3. In `run_sync_once`, add a last `except Exception` that logs the class name, records the run
   with a stop reason and returns exit 1. A new stop value changes the ingestion contract and
   needs an ADR; reusing `garmin_error` doesn't.
4. In `run_schedule`, treat an exception from a run as a failure and back off. Don't exit.
5. Set `pretty_exceptions_show_locals=False` explicitly on the Typer app, so a future default
   change can't leak secrets.
6. Add tests for each path: a non-JSON body, a corrupt zip and a raising run inside the scheduler.

### M1 — A 401 or 403 on downloads marks files `unavailable` for good, and the run reports complete

**Evidence.** `_download` treats any 4xx as "file doesn't exist" (`sync.py:358-361`). A data
request that still gets 401 after one token renewal surfaces as `GarminApiError(401)`
(`garmin/transport.py:117-134`), not as an auth error. Probe I: five activities whose downloads
answer 401 all end as `unavailable`, and the report's `stopped` is `None`.

**Impact.** Each such file costs three requests: the request, a token renewal and the retry. A
300-request run can mark about 95 files as missing during a short auth glitch at Garmin. Only
NULL is ever retried, and no retry command exists (OPEN_QUESTIONS 28), so those files are lost
until someone edits the database.

**Suggested fix.** Mark `unavailable` only for 404 and 410. Make 401 and 403 stop the run, as
`auth_failed` or `garmin_error`. For other 4xx, count an error and leave the status NULL, or
store an attempt count. Add a test.

### M2 — "Missing means NULL" isn't enforced for `activity` and `daily_summary`

**Evidence.** Storage invariant 6 says values out of plausible range become NULL at parsing
(`docs/contracts/storage-schema.md:27`). Sleep, HRV, weight and FIT values are range-checked.
`parse_activities` (`normalize.py:141-168`) and `parse_daily_summaries` (`normalize.py:251-274`)
copy values as they arrive. Only the stress durations go through `_minutes`. Probe F: a daily
summary with stress average −1, stress maximum −2, resting HR 0, maximum HR 900 and Body Battery
minimum −5 is stored exactly so.

**Impact.** Garmin's other wellness APIs use −1 and −2 as "no data" markers for stress. If the
Personal API does the same, those values flow into ADR 0013's resting HR, stress and Body
Battery trends (M6e) and into per-activity metrics. The real-data check counts non-null values
only (`scripts/real_data_check.py:160`), so it wouldn't have seen them. Whether real data
contains them is unknown (OPEN_QUESTIONS 35).

**Suggested fix.** Apply the same `_within` checks: HR 20–250, stress 0–100, Body Battery 0–100,
non-negative durations, distances, steps and calories, training effect 0–5. Add counts of
negative and out-of-range values to the real-data check.

### M3 — Duplicate keys in one payload abort every run at the same chunk

**Evidence.** `_upsert` sends all rows as one multi-row `INSERT … ON CONFLICT DO UPDATE`
(`repository.py:164-167`). If two items in one response share a key, such as the same
`calendarDate` twice, Postgres refuses. Probe D: `CardinalityViolation`, and no cursor is stored.

**Impact.** It raises `SQLAlchemyError`, so the run ends as `database_error`. The cursor doesn't
move, so the next run fails on the same chunk. Daily summaries are fetched first, so sleep, HRV,
weight, activities and FIT parsing are blocked too. Nothing is recorded (L2), so `status` keeps
showing the last good run. Duplicates haven't been seen on real data. The point is that one
odd response stops all syncing for good.

**Suggested fix.** De-duplicate rows by key in `_upsert`, keeping the last, and count dropped
duplicates as `skipped`. Add a test.

### M4 — The scheduler and app containers get the Postgres superuser password

**Evidence.** `scheduler` and `app` load the whole `.env` through `env_file`
(`docker-compose.yml:40-42`, `66-68`). Per the setup steps that file holds `POSTGRES_PASSWORD`
(`docs/operations.md:16`), and the official Postgres image makes `POSTGRES_USER` a superuser.

**Impact.** SECURITY.md lists "App compromise or bug alters the schema", with the DML-only app
role as the control (`SECURITY.md:45`). That holds for bugs. Code running in the app container,
though, can read the owner's password from its own environment and connect as superuser, which
allows DDL and `COPY … PROGRAM` in the database container. The app container already holds the
Garmin key, so what this adds is control over the database and the database container. AWS is
planned with separate secrets (ADR 0008); this is local only.

**Suggested fix.** Give the app containers only what they need. Either list the variables under
`environment:` or keep a separate env file without `POSTGRES_PASSWORD`, while compose
interpolation still reads `.env`. Consider a non-superuser schema owner for migrations, created
by the init script. Update SECURITY.md.

### L1 — Values too long or too large for their columns wedge sync or FIT parsing

Garmin strings go into `String(64)` columns unchecked: `sport`, `event_type` and
`training_effect_label` (`storage/models.py:128-129`, `142`). Probe J: a 65-character
`activityType.typeKey` raises `StringDataRightTruncation`. In FIT files, the lap
`start_offset_s` isn't range-checked (`fit.py:150`) and goes into an `Integer` column
(`storage/models.py:226`). Probe E: a CRC-valid file whose lap starts far from its first record
gives an offset of about 4.0 × 10⁹. Probe E2: two runs both end with `DataError`, and the file
stays unparsed. `parse_pending_fit_files` catches only `FitParseError` and `FileNotFoundError`
(`sync.py:397-404`), so the same file fails every run. The same holds for `elapsed_s`. The
triggers are unlikely, but the outcome is the same stuck state as M3.

**Fix:** check string shape and length in `normalize`, as HRV status already is, and
range-check offsets and elapsed seconds. Catch database errors per FIT file, roll back and mark
the file `invalid`.

### L2 — `database_error` is never recorded in `sync_run`, though the docs say it is

The run is recorded inside the `try` that catches `SQLAlchemyError` (`runner.py:89-101`), so a
database error never writes a row. The ingestion contract names only `already_running` as
unrecorded, and operations.md says the health check turns unhealthy when the last run ended in
`database_error` (`docs/operations.md:75`). In practice, after a database error caused by data
(M3, L1), `status` reports the previous run as complete and healthy until the 13-hour age limit
passes.

**Fix:** record the failed run in a new session, best effort, or correct the docs.

### L3 — The advisory-lock connection stays "idle in transaction" for the whole run

`sync_lock` runs `pg_try_advisory_lock` on a plain `engine.connect()` (`storage/db.py:40-46`).
SQLAlchemy starts a transaction automatically, so the connection is idle in transaction for the
run. Probe G confirms it in `pg_stat_activity`. Where the server sets
`idle_in_transaction_session_timeout`, a common hardening setting that RDS leaves off by default,
the server kills that connection mid-run. The lock then disappears silently, and the unlock in
`finally` raises.

**Fix:** open the lock connection with `isolation_level="AUTOCOMMIT"`.

### L4 — After an interrupted parse, an activity's second FIT file replaces the first

The contract says the first file by name is used and the rest are `skipped`. Only files parsed
in the current run are remembered (`sync.py:381-396`). If a run stops after `a.fit` is parsed and
before `b.fit` is marked, the next run parses `b.fit` and replaces the samples. Probe H: the
activity ends with `b.fit`'s 90 samples instead of `a.fit`'s 30, and both files are marked
`parsed`.

**Fix:** decide in SQL whether an activity already has a parsed file at the current parser
version.

### L5 — Migrations 0003–0005 don't grant their tables, contrary to invariant 7

Invariant 7 says "a new table's migration must grant it, and test it in `test_privileges.py`"
(`docs/contracts/storage-schema.md:29`). Migrations 0003–0005 instead rely on the default
privileges set in 0001 (`20261028_0003_…py:7`). Those apply only to tables created by the same
role, and only if the app role existed when 0001 ran; otherwise 0001 just raises a notice
(`20261025_0001_…py:200`). `test_privileges.py:14-20` checks `athlete` only. On AWS, if the app
role is created after the first `db-upgrade`, or migrations later run as another role, new tables
end up without grants. That fails closed, but only at runtime.

**Fix:** an idempotent grant helper called from every migration, and a privileges test that
covers every table.

### L6 — The schema guard checks column names against a hand-kept table list

`test_schema.py:5-15` checks names in the `NORMALIZED_TABLES` tuple against a short word list.
A new table missing from the tuple isn't checked. The forbidden words don't include `gps`,
`position`, `coord` or `title`. Free-form strings under neutral names (`sport`, `event_type`,
`training_effect_label`) pass without any shape check, while HRV `status` gets one.

**Fix:** treat every table outside an explicit raw and operational allowlist as normalized, and
require each `String` column to be on a reviewed allowlist.

### L7 — Credential-shaped literal in CI

`.github/workflows/ci.yml:40` and `:50` set a fixed Postgres password and embed it in
`TEST_DATABASE_URL`. CLAUDE.md forbids credential-shaped literals, even fake ones. The
service is throwaway, so the risk is low; it's a rule violation that scanners may flag.

**Fix:** generate the password in a step, mask it, write it to `$GITHUB_ENV` and build
`TEST_DATABASE_URL` from it.

### L8 — Failure paths and CLI commands without tests

Coverage shows these lines never run under the suite:

- the `database_error` path (`runner.py:99-101`)
- the mapping of `rate_limited` to exit code 75 (`runner.py:105`)
- the zip size cap (`garmin/files.py:35-37`)
- a missing blob during parsing (`sync.py:402-404`)
- the `schedule` command (`cli.py:144-167`)
- the database-failure branches of `db-upgrade`, `purge-data`, `status` and `parse-fit`

No test feeds a non-JSON body, a corrupt zip or duplicate keys (H1, M3). The leak check inspects
log records only (`tests/conftest.py:25-49`), not what the CLI prints to stdout or stderr.

### L9 — Pacing and retry settings have no bounds

`garmin_min_request_interval` and `garmin_max_retries` (`config.py:35-36`) accept 0 or negative
values. `GARMIN_MIN_REQUEST_INTERVAL=0` turns pacing off, against the 1 request per second in the
contract and in ADR 0002's account-safety reasoning.

**Fix:** `Field(ge=1.0)`, or a stated floor, and `ge=0, le=10` for retries.

### L10 — Activities deleted at Garmin are never removed

Sync only upserts (`repository.py:126-129`). An activity deleted or merged in Garmin Connect
stays in `activity`, `activity_sample` and `activity_lap` for good. That inflates phase 2's
load and fitness numbers. It's a design gap rather than a bug (OPEN_QUESTIONS 36).

### L11 — Doc drift

- `PROJECT_STATE.md` lists merged PRs as #2–#8, but #10–#16 are merged too. Its AWS plan row
  still says it waits on the owner's merge, although #10 merged it.
- `OPEN_QUESTIONS.md` refers to an item 33 that doesn't exist. The new items here start at 34.
- The ingestion contract says a 4xx skips only that dataset. That holds for range datasets. For
  activities, a 4xx stops the run as `garmin_error` (`sync.py:183`, `193`).

## Contract invariants

| Invariant | Holds? | Enforced by a test? |
|---|---|---|
| No GPS or free text in normalized tables | Yes for column names. Strings aren't shape-checked (L6) | Name-based, `test_schema.py:18` |
| `athlete_id` with cascade on every data table | Yes | `test_schema.py:25` |
| App role is DML only | Yes on the tested paths | `test_privileges.py` (only `athlete` for read and write, L5) |
| Missing means NULL | Sleep, HRV, weight and FIT yes; activity and daily summary no (M2) | Partly |
| Upserts set `updated_at` | Yes (`repository.py:167`) | `test_repository.py:92` |
| `raw_payload_id` is `SET NULL` | Yes | Migration drift check |
| Logs and `sync_run` carry counts only | Yes: messages pass counts, dates and class names only, and database errors log the class name only (`runner.py:100`) | `test_sync.py:296`, `test_runner.py:52`, the leak fixture |
| Migrations are linear, numbered and run as the owner | Yes | `test_migrations.py` (drift and round trip) |

## Security rules in practice

Checked and found as documented:

- **Secrets:** the API key and both database URLs are `SecretStr` (`config.py:32`, `39-40`). The
  token has `repr=False` (`garmin/token.py:23`). The key is unwrapped only where it's sent
  (`garmin/transport.py:100`). `config` masks secrets, and the TLS warning logs the host only.
- **Errors:** they carry the method, path and status only (`garmin/errors.py:24`,
  `garmin/transport.py:108`, `123`, `127`). The httpx and httpcore loggers stay at WARNING
  (`log.py:37-38`).
- **SQL:** SQLAlchemy Core with bound parameters throughout. The only interpolated identifier,
  the app role, is checked against `ROLE_NAME` (`config.py:26`, migration 0001 line 179).
- **FIT and zip caps:** 64 MiB and 400,000 records, with an integrity check first and SDK calls
  guarded (`fit.py:101-118`, `121-127`). The zip is capped at 200 MiB by declared size
  (`garmin/files.py:34`). What happens after the cap is H1.
- **Blob keys:** built from IDs and hashes, validated, and resolved inside the store root
  (`storage/blobs.py:19`, `75-79`).
- **Actions:** every action is pinned to a full SHA with a version comment.
  `persist-credentials: false` is set on every checkout. The top-level permission is
  `contents: read`, and only the `image` job adds `packages: write`. Trivy runs from its pinned
  image after cosign verifies it.
- **Images:** pinned by digest in the `Dockerfile`, compose and CI. The runtime image runs as a
  non-root user without pip. Postgres listens on 127.0.0.1 only.
- **Credential-shaped literals:** none in `src/` or `tests/`, where test passwords are made at
  runtime. CI has one (L7).

Notes that aren't findings:

- The image the `scan` job checks and the image the `image` job pushes are two separate builds
  of the same commit (`ci.yml:91-104` and `130-137`). They share pinned inputs and the cache,
  but the pushed bytes aren't the scanned bytes.
- The local TLS check only warns, which is known and planned for B3b.

## What wasn't checked

- `.env.example`: the rules forbid reading `.env*`, so the template wasn't checked for
  credential-shaped values.
- Behaviour on real Garmin data or against the owner's database. The suite and the probes are
  synthetic. Whether Garmin sends sentinel values (M2) or duplicate keys (M3) is unknown.
- POSIX file permissions in the blob store: the test is skipped on Windows, and CI on Linux runs
  it.
- The Docker image build, Trivy and cosign: not run locally. They're green in CI on `main`.
- The `uses: $/.github/workflows/audit.yml` form in `ci.yml:64`: GitHub runs it on `main`, but
  its semantics weren't looked up.
- GitHub settings (ruleset, secret scanning, CodeQL), the GHCR image's visibility and
  `pip-audit` results: owner-side, or outside git.
- The phase 2 and AWS documents: out of scope.

## Re-check of the fixes (2026-11-04)

A fresh subagent checked `main` at `f845ae7` (#19) against H1 and M1–M4, reading the code and
tests and running the non-Postgres tests (101 passed); CI on #19, including the Postgres tests,
was green. The planning session confirmed its new findings in the code, and reproduced N2.

**Verdict: phase 1 is done.** No high finding is unresolved, and nothing new is high.

| Finding | Verdict | Test that fails if the fix is reverted |
|---|---|---|
| H1 | Resolved: non-JSON bodies and unreadable zips become Garmin errors; other exceptions end the run as `internal_error`; the scheduler backs off instead of exiting; `KeyboardInterrupt` and `SystemExit` still stop it | client tests for non-JSON and corrupt zips; sync tests for both, and for `internal_error`; runner and scheduler tests |
| M1 | Resolved: only 404/410 mark a file `unavailable` | sync tests for 401/403 and another 4xx (410 untested) |
| M2 | Resolved: every numeric field of activities and daily summaries is range-checked; legitimate zeros and Body Battery sums above 100 are kept | normalization tests |
| M3 | Resolved: the last row per key wins | repository test |
| M4 | Resolved for `POSTGRES_PASSWORD` and `MIGRATION_DATABASE_URL`; no documented command breaks | none (compose) |

New findings, all low (tracked in OPEN_QUESTIONS 34):

- **N1** (from the M1 fix): 50 or more downloads that keep answering another 4xx end the download
  loop early, so older files are never tried while the run reports complete.
- **N2** (gap in H1's zip handling): a zip with the UTF-8 name flag and an invalid name raises
  `UnicodeDecodeError`, which ends every run at that file as `internal_error`.
- **N3** (doc drift, like L2): an `internal_error` raised outside `Syncer.run` isn't recorded in
  `sync_run`. The ingestion contract now says so.

Observation, not new: a download answering 200 with an HTML page is still marked `invalid` for
good, the same "a glitch writes files off" pattern as M1 on a success status.
