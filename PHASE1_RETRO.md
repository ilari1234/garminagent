# Phase 1 retrospective

Written 2026-11-01. Phase 1 ran 2026-10-24 to 2026-10-28 in one long Claude Code session.
**The code is merged, but phase 1 is not yet "done"** under the rule in ADR 0012: no independent
review against the plan and contracts has happened, and real-data confirmation is partial
(OPEN_QUESTIONS).

## What was delivered

| Milestone | Delivered | PR |
|---|---|---|
| M0 spike | Probe of every endpoint, [evaluation](docs/garmin-personal-api/evaluation.md) | direct to `main` (before the ruleset) |
| M1 scaffold | Project layout, ruff, pyright, pytest, CLI, CI, Dockerfile, compose | direct to `main` (before the ruleset) |
| M2 client | Token handling, 26 endpoints, pacing, backoff, file validation | direct to `main` (before the ruleset) |
| Supply chain | Pinning, Dependabot, pip-audit, zizmor, Trivy, cosign | direct to `main` (before the ruleset) |
| PR workflow | Public-repo settings and PR rules documented | [#2](https://github.com/ilari1234/garminagent/pull/2) |
| M3 storage | Schema, migrations, raw store, roles, `purge-data` | [#3](https://github.com/ilari1234/garminagent/pull/3) |
| M4a sync | Backfill, incremental, budget, activity files | [#4](https://github.com/ilari1234/garminagent/pull/4) |
| M4b FIT | Samples and laps via `garmin-fit-sdk` | [#5](https://github.com/ilari1234/garminagent/pull/5) |
| M4c wellness | Sleep, HRV, weight, stress and Body Battery | [#6](https://github.com/ilari1234/garminagent/pull/6) |
| M5 operations | Scheduler, run history, `status`, operations guide | [#7](https://github.com/ilari1234/garminagent/pull/7) |

## Deviations from the first plan

| Planned | Actual | Why |
|---|---|---|
| Private repo | Public | Rulesets need it on this plan (ADR 0010) |
| `sync/` package, `backfill` command | `sync.py`, `runner.py`, `fit.py`, `normalize.py`; `sync --since` | One code path; no need for a package |
| Separate endpoints for stress and Body Battery | From daily summaries | No extra requests |
| Normalized splits table | Not built; `activity_lap` from FIT instead | FIT laps cover the need. Activity-detail splits aren't synced |
| Secrets from env or a mounted file | Env only | Enough locally; Secrets Manager maps to env |
| Logs to stdout | Logs to stderr, JSON results on stdout | Keeps command output parseable |
| Python 3.12 | 3.15 | Owner's change |
| `user_id` | `athlete_id` | Naming |
| M4 as one milestone | M4a, b, c | Reviewable PRs |

Endpoints implemented in the client but **not synced:** HR zones (M6a plans this), fitness age, activity
detail and workouts, intraday HR, steps, stress and Body Battery ranges, SpO2 day, hydration,
nutrition, wellness activities, weight goals, menstrual data, user settings.

## Shortcuts and tech debt

- **Local `BlobStore` is hardcoded** at `cli.py` and `runner.py` call sites. B3a fixes this with
  `make_blob_store`.
- **`DB` TLS check only warns.** B3b plans a hard failure on AWS.
- **FIT and wellness are unproven on real data**; tests are synthetic. Weight's grams-or-kilograms
  rule is a guess from Garmin's other APIs.
- **`parse_activities` ignores available fields** such as thresholds and Garmin's power metrics. M6a
  plans re-normalization.
- **`activity_sample` has no pause flag**, and `elapsed_s` includes gaps. Phase 2 must define a rule.
- **Rate limits are unknown**; pacing is a guess that hasn't failed so far.
- **Migrations have only been tested** on throwaway databases and the owner's fresh stack.
- **Docs drifted during the phase.** Fixed in the handoff change; keep them current in each PR.
- **No end-to-end test on real data in CI**, on purpose: no live network calls.
- **Dependabot's uv updates don't work** on Python 3.15, so lock upgrades are manual.

## What went wrong, and the lesson

| Event | Lesson now in the code or rules |
|---|---|
| Spike crashed: Garmin returns the token as plain text | Probe first, assume nothing; parser accepts both |
| Sync stalled at small budgets | Page-level progress; minimum budget of 10 |
| Leak test missed DEBUG records because they were never created | Its fixture lowers root to DEBUG |
| Silent progress looked like a hang | INFO progress logs with counts only |
| GitGuardian flagged compose defaults, a fake test URL, then `${VAR:?}` in URLs | No default passwords; plain `${VAR}`; runtime-built test URLs. Passwords were rotated |
| One bad FIT file could crash every run | SDK calls guarded; file marked `invalid` |
| `status` rollback caused `DetachedInstanceError` | `status` is read-only |
| `setup-uv@v10` has no major tag | Pin by SHA from the release |
| Trivy found vulnerable libraries in the base image's pip | pip removed from the runtime image |
| cosign 3.1.3 trusted a distrusted root | 3.1.4; check tool images' CA stores |

## What Phase 2 must know

1. **Read normalized tables only**, per the [storage contract](docs/contracts/storage-schema.md). The
   exception is `renormalize`, which reads `raw_payload` to fill normalized columns.
2. **Only one track adds migrations.** The next number is 0006; the app role needs grants on new
   tables in the same migration.
3. **`sync_run.counts` and logs carry counts only.** Don't log metric values. The leak and schema guard
   tests must cover new tables.
4. **`replace_activity_samples` deletes and re-inserts** on re-parse, so anything derived from samples
   must be recomputed when `parser_version` changes.
5. **`upsert` always bumps `updated_at`**, even if no value changed, and activities are refetched for
   7 days. An `updated_at` trigger for recompute will fire on recent activities on every run. M6b's
   recompute rule must account for this.
6. **Sport strings are Garmin's** (`activity.sport`) and unmapped. Phase 2 needs a sport grouping.
7. **`start_time_local` can be NULL.** Fall back to UTC, as the proposal does.
8. **Real data hasn't validated any of this yet.** Do the real-data check first (OPEN_QUESTIONS 1).
9. **`Sample` values are range-checked** in `fit.py`: out of range becomes NULL, so metrics must
   handle NULLs.
