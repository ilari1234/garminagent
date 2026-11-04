# 0015: Sync failure handling after the phase 1 review

**Status:** Accepted (owner's decision, 2026-11-04: fix H1 and M1–M4 of the
[phase 1 review](../docs/reviews/phase1-review.md)). Adds to ADR 0002 and the ingestion contract.

## Context

The independent review found failure paths that broke the ingestion contract's rule that bad
input must not fail the run:

- **H1:** exceptions other than Garmin and database errors (a 200 response that isn't JSON, a zip
  that can't be read) escaped the run, and the scheduler loop exited. Compose restarted it, so it
  synced again at once, repeating Garmin requests on every restart. A corrupt zip failed every run
  on the same file.
- **M1:** any 4xx on a download marked the file `unavailable` for good, including a 401 or 403
  during an auth glitch, while the run reported complete.
- **M2:** activities and daily summaries were stored without range checks, unlike the other
  datasets, so "no data" markers such as −1 could reach analytics.
- **M3:** two items with the same key in one response made the upsert fail on every run at the
  same chunk.
- **M4:** the scheduler and app containers received the schema owner's (superuser) password from
  `.env`.

## Decision

- **A new stop value, `internal_error`,** for an exception no handler expected. `Syncer.run` stops
  the Garmin part of the run with it, still parses FIT files, and the run is recorded. The runner
  returns it with exit code 1 when the failure is outside `Syncer.run`. Logs name the exception
  class only, never its message. It counts as a failure for the health check and backs off like
  `garmin_error`.
- **The scheduler loop never exits on an error.** An exception from a run is an `internal_error`
  run and backs off; only a stop request ends the loop.
- **Unusable Garmin responses become Garmin errors.** A success status with a body that isn't
  JSON raises `GarminResponseError` (method, path and status only). A zip whose members can't be
  read raises `UnexpectedFileError` with reason `corrupt_zip`, so the file is marked `invalid`.
- **Download answers:** only 404 and 410 mark a file `unavailable`. 401, 403 and 5xx stop the run
  (`garmin_error`) and leave the file untried. Other 4xx leave it untried too, count as `errors`, and
  it's tried once per run until it resolves.
- **Every normalized dataset is range-checked:** activities and daily summaries now turn
  out-of-range values into NULL, as sleep, HRV, weight and FIT values already did.
- **Upserts keep the last row per key** within one batch, and log how many repeated.
- **The app containers get no owner credentials:** compose blanks `POSTGRES_PASSWORD` and
  `MIGRATION_DATABASE_URL` for `scheduler` and `app`; only `migrate` and `db` see them.
- **Tracebacks never show local variables:** `pretty_exceptions_show_locals=False` is set on the CLI.

## Alternatives considered

- **Reuse `garmin_error` for unexpected exceptions.** Rejected: it would send the owner to look
  for Garmin trouble when the cause is a bug or an unexpected input.
- **Store an attempt count for downloads refused with other 4xx.** Deferred: no such refusals have
  been seen on real data, and one request per affected file per run is a small cost.
- **A separate `.env` file for the app containers.** Rejected for now: blanking two variables keeps
  the owner's setup unchanged. A non-superuser schema owner would harden this further (review M4).

## Consequences

- Monitoring and the AWS alarms gain one stop value (`internal_error`, exit code 1).
- Rows stored before this change keep any out-of-range values until they are re-normalized. Recent
  days and activities are rewritten by the next runs; older rows wait for M6a's `renormalize`.
- An activity whose download keeps answering another 4xx costs one request per run.
