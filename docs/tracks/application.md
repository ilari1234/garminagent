# Track A: application

Entry point for a session working on `src/` and migrations (phase 2 now: M6c–M6f). Read this
instead of every doc; follow the links only for what your milestone touches.

## Read first

1. [CLAUDE.md](../../CLAUDE.md) (loaded for you) and the Status table in
   [PROJECT_STATE.md](../../PROJECT_STATE.md) on `origin/main`, or run `/session-start`.
2. [ADR 0013](../../decisions/0013-analytics-design.md): the metric definitions, milestones and
   the owner's decisions. Read its *Decision* section and your milestone's part.
3. [docs/build-notes/analytics.md](../build-notes/analytics.md): what M6a and M6b built and the
   choices they made. Your milestone builds on these.
4. [docs/contracts/storage-schema.md](../contracts/storage-schema.md): which tables you may read
   and the invariants new tables must keep.
5. Open questions your milestone touches in [OPEN_QUESTIONS.md](../../OPEN_QUESTIONS.md)
   (40: lab test input, 41: decoupling steadiness).

Only if needed: [docs/plan.md](../plan.md) (phase 2 milestones, data handling for phase 3),
[SECURITY.md](../../SECURITY.md) (its M6a and M6b reviews), the
[ingestion contract](../contracts/ingestion.md) (CLI and configuration).

## What this track owns

- `src/garminagent/`, `tests/`, and the only Alembic migration chain. Check the next number with
  `ls src/garminagent/storage/migrations/versions/`.
- Not `infra/`: that's track B. Both tracks touch `cli.py`, `config.py`, `pyproject.toml` and
  `uv.lock` (track B's app code, like B3b); rebase over the other track's merges.

## Rules that bite in this track

- **Read normalized tables only.** Only `renormalize` reads `raw_payload`.
- **A new table:** grants to the app role in the same migration (`tests/storage/test_privileges.py`),
  and the schema guard and leak tests must cover it.
- **Counts only** in logs, `sync_run.counts` and command output; never metric values.
- **Values can be NULL.** Samples are range-checked (out of range is NULL), `start_time_local`
  can be NULL (fall back to UTC).
- **Recompute when inputs change.** Re-parsing replaces samples, and `upsert` bumps `updated_at`
  on every refetch, so derive recompute from fingerprints, not timestamps (as M6b does).
- **Real data runs are the owner's.** `renormalize`, `analyze` and `scripts/real_data_check.py`
  against the real database run in the owner's terminal. You may add counts-only sections to the
  check script. Hand over the exact commands in a fenced block, say which counts to check and what
  they should be, and ask the owner to paste back counts only.
- **Phase 3 is coming:** anything that may reach a model later follows *Data handling* in
  docs/plan.md (derived metrics only, no GPS, free text or IDs).

## Finishing a milestone

Run the `milestone-close` skill. For the last milestone of phase 2, it ends with the `reviewer`
subagent.
