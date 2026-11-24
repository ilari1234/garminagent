# M6e review

Independent review under ADR 0012, 2026-11-24, by the `reviewer` subagent. It compares the M6e
change (branch `m6e-recovery`, base `origin/main` 895ac54) with ADR 0013 (the *Recovery* row and
*Owner's decisions on M6e*), docs/plan.md M6e, docs/contracts/storage-schema.md, the build notes'
*M6e as built*, OPEN_QUESTIONS 31, SECURITY.md's *M6e analytics review* and PROJECT_STATE.md.

The review ran against the band as first decided on 2026-11-24 (±0.5 SD of the 7-day means in
the baseline). Its M1 led the owner to change that the same day; see *Resolution*.

## Verdict

**Done**, with no high finding. The code implemented the owner's decisions as written: HRV as
ln(`last_night_avg`); the recent mean over 7 days ending on the day with at least 4; the
baseline over days −66..−7 with at least 30; ±0.5 SD with the edge `within`; HRV and resting HR
judged separately; sleep and weight left out. Every hand-computed test value checked out.
Grants, purge, schema guard and counts-only output hold. One medium finding concerned how the
decided band behaves, not a code bug.

| Part | Verdict | Findings |
|---|---|---|
| Pure series (`analytics/daily_recovery.py`) | Matches the owner's decisions | M1, L2 |
| Recompute and minimal writes | Matches M6c's pattern | none |
| Migration 0010, model, grants | Correct | L4 |
| Purge, schema guard, leak tests, counts only | Holds | none |
| Real-data check `recovery` section | Counts only | none |
| Docs consistency | Mostly consistent | L1, L3 |

## Scope and method

Read CLAUDE.md, the full diff and the new files; `daily_load.py` (the M6c pattern); the
`normalize.py` range checks for `resting_hr` and `last_night_avg`; `test_migrations.py`; ADR
0013; the plan; the agent-layer contract; SECURITY.md's milestone checklist. Ran
`uv run pytest tests/test_daily_recovery.py` (15 passed) and `uv lock --check` (no new
dependency). Ruff, pyright and the Postgres suite were reported clean by the caller (634 passed,
1 skipped, against a throwaway Postgres 18); the reviewer had no `TEST_DATABASE_URL` and started
no container. Probes on the pure functions used synthetic data only: 5 years of nights take
about 1.1 s; 730 days of steady HRV and resting HR over three seeds gave the flag shares in M1;
whole-bpm resting HR gave L2.

## Findings

| ID | Sev | Finding | Part |
|---|---|---|---|
| M1 | Medium | ±0.5 SD of the 7-day means flags about two thirds of days on steady noise, undocumented | Pure series, docs |
| L1 | Low | The agent-layer contract and plan.md say more was dropped than ADR 0013 records | Docs |
| L2 | Low | A zero or tiny baseline SD with whole-bpm resting HR flags a 1 bpm change | Pure series |
| L3 | Low | The contract's "baseline's day count" didn't say it counts 7-day means | Docs |
| L4 | Low | The resting HR CHECK constraints were never exercised against the database | Tests |

**M1.** The recent value and the baseline sample were the same kind of number (7-day means), so
with nothing changing about 62 % of days fall outside ±0.5 SD. Probes: HRV 66–70 % and resting
HR 61–67 % flagged over three seeds. Every doc called `within` "the usual range" without saying
it was the minority state. Fix: tell the owner, document the expected split; any band change is
an owner decision.

**L1.** `docs/contracts/agent-layer.md` lists `recovery_trend` with sleep, Body Battery and stress
trends, and docs/plan.md credits ADR 0013 with dropping Body Battery, stress and "Garmin's status
alongside", but the ADR's bullet names only sleep and weight. Fix: confirm with the owner, then
align the ADR and the contract.

**L2.** With resting HR held at 45 bpm and one night at 46, the SD is 0 and the day flags
`above`; with a 46 every tenth night, the SD is about 0.07 bpm and an ordinary week flags
`below`. Fix: document; a minimum SD would be a new owner decision.

**L3.** `storage-schema.md` said "the baseline's day count"; the code counted days with a 7-day
mean. Fix: say what is counted.

**L4.** Only the HRV CHECK constraints were tested in the database, and Alembic's drift check
doesn't compare CHECKs. Fix: parametrize the refusal test over both measures.

## Contract invariants

| Invariant | Holds? | Evidence |
|---|---|---|
| Every data table belongs to an athlete and cascades | Yes | FK `ondelete=CASCADE` in model and migration; schema guard |
| Derived text columns hold codes only | Yes | Trend CHECKs; `daily_recovery` in the code-column and `DERIVED_TABLES` guards |
| No location or free-text columns | Yes | FORBIDDEN-name guard covers the table |
| No HR-load coefficient stored | Yes | Table added to the coefficient guard |
| Invariant 7: explicit grants | Yes | 0010 grants DML only after `ROLE_NAME` validation; `("0009", NEW_IN_0010)` case; TRUNCATE, ALTER, DROP refused |
| Purge counts every athlete table | Yes | `DailyRecovery` in `purge_athlete`; the purge test asserts the full set |
| Derived tables written by `analyze` only | Yes | Only `update_daily_recovery` writes, from `step.py` |
| Full recompute, minimal writes | Yes | Compare at 1e-12, upsert differences, delete dropped rows, one transaction; tested |
| A trend exists only with its inputs | Yes | `*_trend_inputs` CHECKs match `_trend()` |
| Results independent of row order | Yes | `math.fsum`; order test |

## Security checklist

No new secret. New personal data: derived health trends in the database only; no log line
prints a value, `analyze` counts are integers (tested, including that trend words don't appear),
and the real-data check prints counts and upper-case Garmin status codes only (tested). Inputs
are range-checked normalized rows; a zero HRV is dropped before the log. No new dependency.
`/security-review` recorded with no findings; its claims hold against the code. Threat model
unchanged.

## What wasn't checked

The Postgres tests and ruff/pyright (reported by the caller), the real-data run (the owner's next
step), and the primary source for the 0.5 factor (OPEN_QUESTIONS 31).

## Resolution (2026-11-24, same PR)

- **M1, fixed by an owner decision.** The development session repeated the probe (synthetic
  steady nights, three seeds): ±0.5 SD of 7-day means flagged 68–70 % of days, ±1.0 SD of 7-day
  means 38–41 %, ±0.5 SD of single days 17–25 %; all three caught a sustained 10 % HRV drop within
  2 to 9 days. The owner chose ±0.5 SD of single days. The code, tests (re-derived by hand), ADR
  0013, build notes, operations doc, storage contract and OPEN_QUESTIONS 31 now say so and give
  the expected flag share. `algo_version` stays 1: no row was stored with the first rule.
- **L1, fixed by an owner decision.** The owner confirmed that Body Battery and stress trends
  stay out of M6e like sleep; ADR 0013's bullet now names all four, and `agent-layer.md`'s
  `recovery_trend` lists only what `daily_recovery` holds.
- **L2, documented** in the module, ADR 0013 and the build notes; no minimum SD without an owner
  decision.
- **L3, fixed.** With single days the count is of nights again, and the contract says so.
- **L4, fixed.** The refusal test covers both measures' CHECKs, with a free-text value refused and
  a complete row accepted.
