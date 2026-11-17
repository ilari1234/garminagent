# M6c review

Independent review (ADR 0012) by the `reviewer` subagent, 2026-11-12, of the M6c change on branch
`m6c-daily-load` (base `origin/main` 999c84c) against ADR 0013 including *Owner's decisions on
M6c (2026-11-12)*, the M6c build notes, the storage-schema and agent-layer contracts, docs/plan.md
phase 2 and SECURITY.md. Fixes were applied in the same PR before merge (2026-11-17); the
*Outcome* column records them.

## Verdict

**Done, with one medium finding to fix before M6f builds on `daily_load`; fixed.** Formulas match
ADR 0013 and the owner's decisions: 42- and 7-day exponential averages; form as the previous day's
fitness minus fatigue; ramp as the 7-day fitness change, NULL for the first 7 days; the series
from the first activity to the later of the last activity and the last daily summary; `all` plus
one series per trained group; missing load counted; reliability from 126 days; ISO weeks in the
view. Grants, the CHECK constraint, cascade on athlete delete and counts-only output hold.

| Part | Verdict | Findings |
|---|---|---|
| Series computation (`analytics/daily_load.py`) | Correct formulas; write minimisation was order-dependent | M1, L4 |
| Migration 0008, model, grants | Holds | L2, L6 |
| Weekly view `daily_load_weekly` | Matches the decision | L1 |
| `analyze` step, counts, CLI | Counts only | L5 |
| Tests | Green; some gaps | L4, L6 |
| Docs (ADR, build notes, contract, ops, state) | Mostly consistent | L2, L3, L5 |

## Scope and method

Read CLAUDE.md, PROJECT_STATE.md, ADR 0013 in full, the M6c build notes, the storage-schema and
agent-layer contracts, docs/plan.md phase 2, the track A entry doc, OPEN_QUESTIONS 10–18 and
SECURITY.md; the whole of `daily_load.py`, migration 0008, the `DailyLoad` model, the `step.py`
and `cli.py` changes, `repository.purge_athlete` and M6b's day assignment; the new and changed
tests. Ran ruff, ruff format, pyright strict (all clean) and pytest without Postgres (437 passed,
116 Postgres tests skipped; `TEST_DATABASE_URL` unset, and the reviewer was asked not to start a
database). The author ran the Postgres tests locally against a throwaway Postgres 18 container.
One probe with synthetic inputs (60 days, three activities a day, random loads) compared the
series for shuffled input. No real data, `.env*`, `data/` or Garmin API was touched.

## Findings

| ID | Sev | Finding | Outcome |
|---|---|---|---|
| M1 | medium | Daily sums depended on row order (no `ORDER BY`), so unchanged inputs could rewrite rows and break "`updated_at` means last changed" | Fixed |
| L1 | low | `date_trunc('week', date)` resolves to the `timestamptz` variant, so the view's week boundary depends on the session time zone (unconfirmed) | Fixed |
| L2 | low | Contract invariant 7 said "Views are `security_invoker`" unscoped, which conflicts with M6f's agent role reading only the `agent` schema | Fixed |
| L3 | low | Contract invariant 4 ("`updated_at` means last written") not amended for `daily_load` | Fixed |
| L4 | low | Ramp, form and weekly tests compared against the code's own values; no order-independence test; no test above the 1000-row write batch | Fixed |
| L5 | low | Stale `analyze --help`, track A entry doc and `purge_athlete` row counts | Fixed |
| L6 | low | The schema guard doesn't see views (not in `Base.metadata`) | Fixed |

### M1: daily sums depended on row order

`_activity_loads` had no `ORDER BY`, `_DayTotals.add` summed loads in arrival order, and changed
rows were found by exact comparison. Floating-point addition isn't associative for three or more
terms, and Postgres row order changes as every sync rewrites recent activities. The probe found
24 of 240 rows differing after a shuffle (about 1e-14, carried into fitness, fatigue, form and
ramp). Values weren't wrong, but `analyze` could rewrite hundreds of unchanged rows, and the
existing test passed only because it had at most two activities a day. A second, unconfirmed
trigger: exact round-tripping relies on `extra_float_digits` ≥ 1 (the default).

**Fix applied:** loads are collected per day and group and summed with `math.fsum` (exactly
rounded, order-free); rows are compared with a relative and absolute tolerance of 1e-12, which
also covers a server that rounds `float8` output. New tests: the pure series is identical for
reversed and sorted input with order-sensitive loads, and an integration test with three
activities of different loads on one day writes nothing on the second run. The build-notes
sentence is corrected.

### L1: week boundary depended on the session time zone

**Fix applied:** the view truncates `calendar_date::timestamp` in both the SELECT and the
GROUP BY.

### L2: "Views are `security_invoker`" conflicted with M6f

**Fix applied:** invariant 7 now limits this to rollup views in `public` read by roles that
already read the base tables, and leaves the `agent` schema's views to M6f.

### L3: invariant 4 not amended

**Fix applied:** invariant 4 names the exceptions: `activity_metrics` (last computed) and
`daily_load` (last changed).

### L4: test gaps

**Fix applied:** literal checks for form on day 2 (100/42 − 100/7 ≈ −11.90) and ramp on day 8
(100 × (41/42 − (41/42)^8) ≈ 15.15); the order-independence test; an integration test with 1200
rows (three groups plus `all` over 300 days) that crosses the write batch on insert and delete.
The side note on `thresholds_backfilled` (a day with only unloaded activities was flagged) is
resolved by flagging only loads that count, tested, and the ADR wording follows.

### L5: stale docs

**Fix applied:** the `analyze` help names the daily load series; the track A entry doc says
M6d–M6f and lists M6c's build notes; `purge_athlete` counts `activity_metrics` and `daily_load`,
and its test now checks that every athlete table is counted.

### L6: views unguarded

**Fix applied:** an integration test in `tests/storage/test_migrations.py` lists the views in
`public`, requires each to be assigned (`DERIVED_VIEWS`) and applies the forbidden-column check.

## Contract invariants (storage-schema.md)

| Invariant | Holds? | Evidence |
|---|---|---|
| 1. No GPS or free text; text columns are checked codes | Yes | `ck_daily_load_sport_group`; `test_schema.py`; views now guarded (L6) |
| 2. `athlete_id` with ON DELETE CASCADE | Yes | Migration FK; `purge_athlete` |
| 3. Rebuildable from lower layers | Yes | Full rebuild every run from `activity`, `activity_metrics` and `daily_summary` |
| 4. `updated_at` semantics | Documented exception (last changed) | L3, M1 fixed |
| 5. `raw_payload_id` SET NULL | n/a | No such column |
| 6. Missing means NULL | Different by owner's decision: load 0 plus counts; ramp NULL for 7 days | ADR 0013, M6c decisions |
| 7. Explicit grants; views SELECT only | Yes | Revoke then SELECT on the view; privilege tests with and without default privileges |
| 8. Linear numbering; next is 0009 | Yes | `down_revision = "0007"` |

## Security checklist

- **New secret?** None; `daily_load` is also guarded against storing the HR load coefficient.
- **New personal data?** Derived daily load, no new kind beyond `activity_metrics.load`. Output
  is counts only, tested by `test_cli_analyze_prints_counts_only`.
- **External input?** None new; bound parameters scoped by `athlete_id`; the role name in 0008 is
  validated by `ROLE_NAME`.
- **New dependency, action or image?** None.
- **`/security-review`:** recorded in SECURITY.md with no findings; the reviewer found no
  security finding above low.
- **Threat model:** unchanged. L2 was a future access-control trap for M6f, now scoped.

## Not checked by the reviewer

The Postgres integration tests (run by the author instead), real-data behaviour (the owner's run,
in PROJECT_STATE next steps), and the TrainingPeaks ramp sources (not re-fetched).
