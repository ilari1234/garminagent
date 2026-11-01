# 0002: One-shot, budgeted, resumable sync

**Status:** Accepted (built in M4a–M5, merged).

## Context

There are no webhooks (ADR 0001), rate limits are unknown, and a first backfill covers years of
data. A sync can be interrupted at any point and must run the same way locally and on AWS.

## Decision

- **One-shot command.** `garminagent sync` does one run. `garminagent schedule` is a thin local
  loop around the same function; on AWS a scheduler starts the one-shot task.
- **Contiguous synced range per dataset** in `sync_cursor`. A run extends it backwards to the
  start date, newest chunk first, and forwards to today. The cursor moves only after a chunk is
  fully stored, so an interrupted run never leaves a gap.
- **Trailing refetch.** Garmin revises recent days: 3 days for daily datasets, 7 for activities.
- **Request budget per run** (`SYNC_MAX_REQUESTS`, default 300, minimum 10). Running out ends the
  run cleanly as `budget`; the next run continues.
- **Rate limiting stops the whole run** (exit 75). A 4xx from one dataset skips only that dataset
  for that run, so an account without a feature doesn't block the rest.
- **Runs are serialized** by a Postgres advisory lock and recorded in `sync_run` (counts only).
- **Idempotent writes:** upserts, and raw payloads deduplicated by content hash.
- **Stress and Body Battery come from daily summaries.** No extra requests.

## Alternatives considered

- **Long-running daemon only.** Rejected: maps poorly to scheduled Fargate tasks.
- **Separate `backfill` command** (in the first plan). Dropped: `sync --since` does it through the
  same code path.
- **Separate stress and Body Battery endpoints, intraday series.** Rejected or deferred: more
  requests for data phase 2 doesn't need yet.
- **Unbounded runs.** Rejected: risks throttling or locking the only account we have.

## Consequences

- Two liveness bugs shaped the design: a small budget stalled on repeated refetch and an unfinished
  chunk. Fixed by page-level progress (ascending forward, descending backfill) and the minimum
  budget. Keep those tests when touching `sync.py`.
- Refetching costs a few requests on every run, about 7 before any new work.
- Files that can't be fetched are marked and never retried automatically, so they don't burn the
  budget. A later retry would need an explicit command.
- Silent runs looked like hangs, so INFO progress logs exist; they carry counts only.
- New datasets must add a cursor name and raw-payload storage. See
  [ingestion contract](../docs/contracts/ingestion.md).
