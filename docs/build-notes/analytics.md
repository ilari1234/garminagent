# Analytics build notes (phase 2)

What was built for each phase 2 milestone, choices made where
[ADR 0013](../../decisions/0013-analytics-design.md) left room, and what real data showed.
The ADR holds the decisions; this file holds how they were carried out
([ADR 0017](../../decisions/0017-build-notes-out-of-adrs.md)). Each milestone PR adds its own
section. Counts only: this repository is public.

## M6a as built (2026-11-07)

Choices made where ADR 0013 left room. They change no milestone, table or metric
definition above.

- **Threshold sports are `all` or a sport group** (`run`, `ride`, `ski`, `strength`, `other`).
  Garmin's zone sport DEFAULT maps to `all`, RUNNING to `run`, CYCLING to `ride`; SWIMMING and
  ROWING map to no group and stay in `athlete_zones` only (counted as `zones_sports_skipped`).
- **Precedence is by source first, then specificity:** manual for the group, manual for `all`,
  Garmin zones for the group, Garmin zones for `all`, activity values. A manual value for `all`
  therefore outranks Garmin's running zones. Within a source, the latest row on or before the date
  holds; a NULL in a later zones row is not a change.
- **Activity values feed only the sports they describe:** LTHR counts for the activity's own group;
  FTP only from rides (for `ride`), threshold speed only from runs (for `run`). Garmin's FTP is a
  cycling value, and power exists only in ski sessions: carrying it to skiing would produce a wrong
  power load where M6b would otherwise fall back to HR load. The owner can set a ski FTP by hand.
- **Zones are requested with `generateDefaults=false`:** generated (age-based) defaults would
  outrank activity values without being the athlete's.
- **Zone history starts at the first fetch.** `valid_from` is the sync day a change was first
  seen; a second change the same day replaces that day's row. Dates before the first fetch fall
  back to activity values.
- **Derived threshold rows are rebuilt by each `analyze`** from `athlete_zones` and `activity`,
  touching only rows that differ (counts `thresholds_inserted`, `_updated`, `_deleted`). Manual
  rows are never read or written by analytics.
- **`thresholds unset`** was added next to `set` and `list`: without it, a mistaken manual value
  would outrank Garmin's values for good.
- **One range table for every source** (`THRESHOLD_RANGES` in `normalize.py`): LTHR 80–230 bpm, max
  HR 100–250, resting HR 25–120, FTP 30–700 W, threshold speed 0.5–10 m/s. Activity and zone values
  outside become NULL; manual input is refused with exit code 2. `getlactateThresholdSpeed` is
  converted from the spec's cm/ms to m/s. Garmin's TSS (0–2000) and IF (0–3, a fraction) are
  stored as `garmin_tss` and `garmin_if` for cross-checks only.
- **`renormalize` covers all five range datasets and activities**, not only activities, so the
  range checks of ADR 0015 also reach old daily summaries. The newest stored version of each record
  wins; it inserts and updates, never deletes, as a sync does.
- **`renormalize` and `analyze` hold the sync lock** and print `already_running` if a sync holds it,
  so they can't interleave with a sync writing the same rows.
- **The `analyze` step needs no blob store in M6a**; when a later part reads files it gets one from
  `make_blob_store(settings)` (B3a).
- **Migration 0006 grants explicitly**, and a test checks the grants with 0001's default
  privileges revoked.
- What these fields hold on real data is recorded below, under *Real-data findings for M6b*.

## Real-data findings for M6b (2026-11-07)

The real-data check's `thresholds` section (#29) ran on the owner's database after M6a's first
`renormalize activities` and `analyze`. It settles the Garmin questions M6a left open (former
OPEN_QUESTIONS 38). Counts only:

- **Activity threshold fields** across 558 activities: `lactateThresholdBpm` arrives on 3 runs with
  2 distinct values. These are the runs where the watch detected a lactate threshold, not a profile
  value copied into every activity, and they are change points in the timeline as built.
  `getlactateThresholdSpeed` and `maxFtp` never arrive, and no other spelling of a threshold key
  does either. The cm/ms conversion therefore stays unconfirmed, but no phase 2 metric uses
  threshold speed (running load is HR-only).
- **`intensityFactor` and `trainingStressScore`** arrive on 15 ski sessions, which are exactly the
  ski sessions with power. Every IF value is on the fraction scale, so `garmin_if` parsing is right.
  These 15 sessions are what `analyze --check` can compare power load against.
- **HR zones** (`generateDefaults=false`) return one zone set: DEFAULT, method `HR_MAX`, all five
  floors, max HR and LTHR. Resting HR is null because the account has Garmin's resting-HR
  auto-update on (`restingHrAutoUpdateUsed`, a key M6a doesn't parse). The timeline therefore holds
  max HR and LTHR for `all` from 2026-11-07 on, and nothing from zones before that.
- **No ride has power**, which confirms the 2026-11-03 finding.

Consequences for M6b. HR load needs max HR, resting HR and LTHR valid on the activity date (HR reserve
fraction, scaled so 1 h at LTHR = 100):

- **Resting HR has no threshold source.** Zones don't carry it and activities never do. The daily
  summaries carry it on every synced day. Decided in ADR 0013, *Owner's decisions on M6b's inputs*.
- **History before the first zones fetch has no max HR**, and LTHR only for runs from the first
  detection on. Without more input, HR load would be "none" for nearly all of the two-year history,
  and fitness needs 126 days of it (see *Consequences*). Decided in ADR 0013, *Owner's decisions on M6b's inputs*.
- **Ski power load needs a ski FTP set by hand** (`thresholds set ftp … --sport ski`). Without one,
  ski sessions use HR load like everything else.

## M6b as built (2026-11-09)

`activity_metrics` (migration 0007) holds one row per activity, written by the `analyze` step's
third part (`garminagent.analytics.activity_metrics`, formulas in `analytics/metrics.py`).
Choices made where ADR 0013 left room; they change no milestone or table.

- **The gap rule, exactly.** A sample covers the seconds until the next sample when that gap is at
  most 10 s, carrying its values; before a longer gap (a pause) and at the end it covers its own
  second only, so 3600 samples at 1 Hz are an hour. "Moving time" (`moving_s`) is time not paused
  by this rule; a standstill the watch kept recording counts as moving. Seconds whose covering
  sample lacks a value stay empty, never filled from a neighbour.
- **Coverage gates every channel at 50 % of moving time** (`MIN_COVERAGE`): weighted power and
  the power metrics need power on half the seconds, HR load and efficiency need HR on half.
  Missing seconds are not extrapolated, so HR load with 60 % coverage counts only those seconds.
  Coverages are stored as fractions.
- **Weighted power** windows cross neither pauses nor seconds without power. It is reported for
  any group, but **intensity, power load and power levels are never computed for runs**, even with
  power and a manual FTP for `all`: running load is HR-only in phase 2.
- **Power load uses moving time** as `s`. Power levels count each second's own power with
  inclusive upper bounds at 55, 75, 90, 105, 120 and 150 % of FTP.
- **HR load**: x is floored at 0 (HR below resting adds nothing) and not capped at 1 (HR above a
  stale max HR still counts). No HR load without max HR, resting HR and LTHR, a positive reserve
  and LTHR above resting.
- **Chosen load**: power load if computed, else HR load if computed, else none
  (`load_method` `power`, `hr`, `none`). An activity without samples (no FIT file yet, or a file
  that couldn't be used) gets a row with `moving_s` NULL and no load; the row is recomputed when a
  file is parsed.
- **HR zones** use Garmin's floors from `athlete_zones`: the group's own zone sport (RUNNING for
  runs, CYCLING for rides) before DEFAULT, latest row on or before the day, only rows with all
  five floors. Zone 0 (below zone 1) is stored too, so zone seconds add up to the seconds with HR.
  Dates before the first zones fetch use the first fetch's floors (the owner's backward fill,
  applied to floors as to thresholds) and set `thresholds_backfilled`.
- **3-zone split**: below zone 3 is low, zone 3 mid, zones 4–5 high, from HR zones only. A
  convention: Garmin's HR zones don't mark LT1 and LT2 (OPEN_QUESTIONS 40 would give real ones).
- **Efficiency factor** is mean output over mean HR on the seconds having both: power (W) when
  power covers half the session, else speed (m/s); `ef_basis` says which. Plain mean output, not
  weighted power, so the whole-session value and the decoupling halves use one definition.
- **Decoupling is not gated on steadiness.** No published rule says what "steady" is, so the code
  computes it for every session of at least 60 min moving time with an efficiency factor (first
  600 moving seconds excluded, the rest halved by moving seconds). Consumers must read it only for
  steady aerobic sessions, for example by the 3-zone split; M6f decides how (OPEN_QUESTIONS 41).
- **Resting HR and backward fill live in the resolver** (`ThresholdTimeline`), so
  `thresholds list --at` shows what the metrics use, including `backfilled` and a
  `daily_summary` source. The fill also applies to resting HR from threshold rows; the daily
  summaries themselves are never filled back.
- **Thresholds recorded** per row: FTP, LTHR, max and resting HR, resting HR's source, the zone
  sport and `valid_from` of the floors, and `thresholds_backfilled` (any of them filled back).
  The HR load coefficient is not stored; it only feeds the fingerprint.
- **The fingerprint** is SHA-256 over the activity's day and sport group, its FIT files (content
  hash, parse status, parser version), the resolved thresholds with their source, sport, date and
  fill flag, the zone floors, the coefficient and `algo_version`. A reader with every other input
  could tell which coefficient was used, but the same reader has HR load and HR samples, which
  tell the same; neither reaches agents.
- **Cap and order**: `ANALYTICS_MAX_ACTIVITIES` (default 200, at least 1) rows per run, newest
  activities first, in transactions of 25; `analyze --full` has no cap. Counts: `metrics_computed`,
  `metrics_current`, `metrics_pending`, `metrics_no_samples`, `metrics_backfilled`,
  `hr_load_not_configured` (this run), and `load_power`, `load_hr`, `load_none` over all rows. An
  error in one activity's arithmetic is counted under `errors` and skips that activity only.
- **Settings**: `ANALYTICS_TRIMP_B` is a float that must equal 1.92 or 1.67; `config` shows it as
  `set`. Settings validation errors no longer echo the input (`hide_input_in_errors`), so a typo
  can't show which coefficient was meant. `status` lists `hr_load_not_configured` under
  `warnings` while it is unset, and `analyze` logs a warning every run; neither changes health.
- **Deleted activities**: the metrics row cascades from `activity`; there is no reconciliation with
  Garmin (ADR 0013, *Owner's decisions on M6b's inputs*).

## M6c as built (2026-11-12)

`daily_load` (migration 0008) holds one row per day and sport group, rebuilt by the `analyze`
step's fourth part (`garminagent.analytics.daily_load`) from `activity_metrics.load`; the view
`daily_load_weekly` rolls it up per ISO week. The owner's decisions (ramp, range, groups, missing
load, reliability, weeks) are in ADR 0013, *Owner's decisions on M6c*. Choices made where the ADR
left room; they change no milestone or metric definition.

- **Full rebuild, minimal writes.** The series is computed from scratch every run and compared
  with the stored rows; only differing rows are upserted and rows outside the new series are
  deleted, all in one transaction. So `daily_load.updated_at` means "last changed", unlike
  `activity_metrics` (last computed) and the normalized tables (last written). Each day's
  load is summed with `math.fsum`, which is exactly rounded, so the result doesn't depend on the
  order Postgres returns activities in (a running sum of three or more loads did, by about 1e-14,
  which the M6c review caught). Rows are compared with a relative tolerance of 1e-12, so a server
  that rounds `float8` output (a lowered `extra_float_digits`) can't make every row look changed.
- **The activity's own day and group** come from `activity` (local start day, else UTC; the
  current sport key), as in M6b, not from the metrics row, which may be stale until recomputed.
  An activity whose metrics row is stale but not yet recomputed (over the per-run cap) keeps its
  stored load until then; only an activity with no metrics row counts as pending.
- **Each group's series starts on the first activity of any group**, not its own first activity.
  Before a group's first session its rows are zeros, which is what a zero start gives anyway, and
  `history_days` and `fitness_reliable` then mean the same in every group.
- **Groups without any activity get no rows** (no ski rows for an athlete who never skis).
- **`moving_s`** sums the metrics' moving time; activities without samples add 0 seconds but
  still count as `sessions`.
- **The weekly view** is `security_invoker`, so it shows only what the reader may read in
  `daily_load`; its migration revokes 0001's default privileges on it (they cover views too) and
  grants SELECT only. Its fitness, fatigue, form, ramp and `fitness_reliable` are the week's last
  stored day's, so the current week shows the latest values; `days` under 7 marks a partial week.
- **Counts:** `daily_load_days` (days in the series), `daily_load_rows`, `_inserted`,
  `_updated`, `_deleted`, `daily_load_reliable_days`, and over the whole series
  `daily_load_activities_unloaded` and `daily_load_activities_pending`. No value leaves the
  database.
- **Hand-computed tests:** a constant load of 100 gives fitness 63.65 after 42 days, fatigue
  100 × (1 − (6/7)^7) after 7, form 100/42 − 100/7 ≈ −11.90 on day 2 and ramp
  100 × (41/42 − (41/42)^8) ≈ 15.15 on day 8; ramp is NULL for the first 7 days; fitness is
  reliable on day 126. Also tested: the series is identical for any input order, and a first run
  above the 1000-row write batch writes and then leaves every row.

What later work must know:

- **Fitness starts from 0 on the first activity's day.** If an older activity is synced later,
  the series start moves and every row changes once. On real data the first ~126 days are
  `fitness_reliable = false`; agents must not read fitness, form or ramp there (M6f's views can
  filter on it).
- **Ramp has no limit in phase 2.** A phase 3 safety limit on it needs a documented source
  (OPEN_QUESTIONS 11).

## Real-data check for M6c (2026-11-17)

The owner ran `analyze` on the real database after migration 0008, with the series already
written by an earlier run. Counts only:

- **751 days, 4506 rows:** 751 × 6, so `all` plus all five sport groups have activities,
  including `other` (Garmin sport keys not yet grouped; `analyze` lists them under
  `unmapped_sport_keys`).
- **626 reliable days:** 751 − 125, as `fitness_reliable` from day 126 implies.
- **0 inserted, 0 updated, 0 deleted** on the rerun: the full rebuild left every stored row as it
  was, including days with several activities, which confirms the review's M1 fix on real data.
- **0 pending, 3 unloaded:** every activity has metrics; three have no load (no samples, or HR on
  under half the session) and add 0 to their days, consistent with M6b's `load_none`.
