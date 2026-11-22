# M6d review

Independent review under ADR 0012, 2026-11-22, by the `reviewer` subagent. It compares the M6d
change (branch `m6d-best-efforts`, base `origin/main` 23189eb) with ADR 0013 (the *Best efforts*
row and *Owner's decisions on M6d (2026-11-22)*), docs/plan.md M6d,
docs/contracts/storage-schema.md, the build notes' *M6d as built*, SECURITY.md's *M6d analytics
review* and PROJECT_STATE.md.

## Verdict

**Done.** No high or medium findings. The shortest-time interpolation is exact: it matched an
independent brute-force search on 300 random uneven tracks (standstills, plateaus, uneven sample
spacing), and power windows matched brute force on 200 random cases. The break rules (pause over
`MAX_GAP_S`, missing distance, counter drop, spike) match the owner's decisions. The fingerprint,
cap, migration, model, grants, views and counts-only output follow the M6b and M6c patterns. The
low findings are edge-case rules, test gaps and doc nits; none blocks M6e.

| Part | Verdict | Findings |
|---|---|---|
| Rules (`analytics/best_efforts.py`) | Correct; exact minimum confirmed by probe | L1, L2, L7 |
| Recompute, fingerprint, cap (`analytics/activity_best_efforts.py`, `step.py`) | Holds | L4 |
| Migration 0009, model, grants | Holds; no drift (`migrate.check` test) | L5 |
| Views `best_effort_curve`, `best_effort_pb` | Match the decisions (ties, periods, previous best) | L4, L6 |
| Counts-only output, real-data check | Counts only | L3 |
| Docs (ADR, build notes, contract, operations, SECURITY, state) | Consistent with the code | L7, L8, L9 |

## Scope and method

Read: CLAUDE.md, PROJECT_STATE.md, ADR 0013's decision section and M6d section, plan.md M6d, the
storage-schema and agent-layer contracts, the M6d build notes, SECURITY.md's M6d entry,
docs/operations.md, the M6c review (for format); all of `best_efforts.py`,
`activity_best_efforts.py` and migration 0009; the `ActivityBestEffort` model; the diffs to
`metrics.py`, `activity_metrics.py`, `step.py`, `cli.py`, `repository.purge_athlete` and
`scripts/real_data_check.py`; `metrics.expand`, `fit.parse_fit` and migration 0008's grant block
for context; both new test files and every changed test.

Ran: `ruff check` (clean), `ruff format --check` (clean), `pyright` (0 errors),
`pytest tests/test_best_efforts.py` (23 passed), full `pytest` without a database (463 passed,
138 Postgres tests skipped; the author's run against a throwaway Postgres: 600 passed, 1
POSIX-only skip). Probes on synthetic tracks only: `shortest_time` and `best_mean` against
brute force (0 mismatches), rolling-sum drift over 5 h (none), timing (a marathon at 1 Hz
0.17 s, a 10 h ride 0.33 s), a single power sample before a 10 s gap, a 1 cm distance dip. No
real data, database or Garmin API was touched.

## Findings

| ID | Sev | Finding | Part |
|---|---|---|---|
| L1 | Low | Any decrease in `distance_m`, even 1 cm, is a counter drop that breaks the track; no tolerance | Rules |
| L2 | Low | Short power windows on smart-recorded files are sample-and-hold: one sample before a 10 s gap fills a whole 5 s window | Rules |
| L3 | Low | Real-data check: a real fast 400 m (under 61.5 s) or long descent flags as implausible; the ride "no distance effort" count includes every ride under 10 km | Real-data check |
| L4 | Low | Views mix algorithm versions while activities are pending after a `BEST_EFFORT_ALGO_VERSION` bump | Recompute, views |
| L5 | Low | No test asserts `security_invoker` on any view (0008's view had the same gap) | Tests |
| L6 | Low | View edge cases untested: same-day tie by activity ID, the period boundary, a second effort on the PB's own day | Tests |
| L7 | Low | Misleading comment in `_first_reached` (its `k == 0` branch can't happen) | Rules |
| L8 | Low | SECURITY.md said every new query is scoped by `athlete_id`; the real-data check's are whole-database by design | Docs |
| L9 | Low | PROJECT_STATE's M6d row had no review link | Docs |

**L1.** `best_efforts.py` treats `step < 0` as a counter drop. A 300 s run at 4 m/s with a 1 cm
dip at t=50 gives `counter_drops = 1` and no 1 km effort (250 s without the dip). FIT distance is
stored in centimetres, so true resets are large; whether watches emit tiny dips is unconfirmed.
Fix only if the owner's real-data run shows many `best_effort_counter_drops`: treat a drop under
1 m as a standstill, bump the algorithm version, add a test.

**L2.** `expand` carries a sample forward up to 10 s, so a single 2999 W sample before a 10 s gap
gives a 5 s best of 2999 W. This is M6b's documented gap rule and the owner chose no extra power
filter, so it's not a defect against the spec; later work should know it.

**L3.** `IMPLAUSIBLE_MPS` applies 6.5 m/s to every run distance, including 400 m, and 16.7 m/s
over 10 km can be reached downhill. A non-zero count needs a look, not necessarily a fix.

**L4.** The views don't filter on `algo_version`; after a version bump, capped recompute leaves
older rows for a few runs. Transient, and `best_effort_pending` shows it.

**L5–L9.** As in the table.

## Contract invariants

| Invariant (storage-schema) | Holds? | Evidence |
|---|---|---|
| 1–2 Layers; agents read no table directly | Yes | Table and views in `public`; no agent role until M6f |
| 3 Derived data rebuildable | Yes | Computed from `activity_sample` and `activity`; `analyze --full` rebuilds |
| 4 `updated_at` means "last computed" | Yes | Rows replaced only on a fingerprint change; tested |
| 6 Missing means NULL | Yes | Not reached is NULL, never 0; CHECK forbids mixed values |
| 7 DML only; explicit grants; views SELECT only; `security_invoker` | Yes | Migration 0009 copies 0008's grant block; privilege tests extended |
| 7 Works under the non-superuser owner | Yes, by inspection | No extensions or foreign `ALTER`; bootstrap test runs it |
| 8 Linear numbering, next 0010 | Yes | `down_revision = "0008"`; contract updated |
| Purge covers every athlete table | Yes | In `purge_athlete`; purge test checks the full set |
| Code columns checked; no location or free text | Yes | CHECKs on `kind`, `sport_group`; schema and view guards updated |
| Model matches migration | Yes | `test_migrations_match_models` |

## Security checklist

No new secrets; only derived times and powers from samples already stored (no GPS, free text or
names); inputs range-checked at parse time, with NULL, gaps, counter drops and spikes tested;
logs, `analyze` counts and the real-data section carry counts only, tested for leaked values;
bound parameters everywhere, with the view and grant SQL interpolating constants and a validated
role; no new dependency, action or image; threat model unchanged. The views hold activity IDs for
the owner's lookups; M6f's agent views must drop them.

## What wasn't checked

Postgres tests by the reviewer (the author ran them green); real Garmin data, so L1 and L3 wait
on the owner's real-data run; `/security-review` (recorded clean in SECURITY.md); performance of
a full `analyze --full` over a long history (single activities take under 0.4 s).

## Resolution (2026-11-22, same PR)

- **L2, L4:** recorded in the build notes' *What later work must know*, for M6f.
- **L3:** the real-data check's docstring and PROJECT_STATE's next step say a non-zero count
  needs a look, and that the ride count includes every ride under 10 km.
- **L5:** `test_rollup_views_run_with_the_readers_privileges` checks `security_invoker=true` on
  all three rollup views.
- **L6:** three integration tests added (period boundary against the database's own
  `CURRENT_DATE`, same-day tie by activity ID, a second effort on the PB's day).
- **L7:** comment and dead branch fixed.
- **L8, L9:** SECURITY.md wording and the PROJECT_STATE review link fixed.
- **L1:** open until the owner's real-data run (PROJECT_STATE next step 2) shows whether
  counter drops are common; recorded in the build notes.
