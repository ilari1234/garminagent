# 0013: Phase 2 metric definitions and storage

**Status:** Accepted (owner's decision, 2026-11-03). The phase 2 planning session produced this on
2026-10-29 and revised it on 2026-11-03 after the orchestrator change (#8) and two real-data runs;
the owner's answers are recorded below. M6a is built (2026-11-07); its choices where this ADR left
room are recorded under *M6a as built*, and the owner's answers on M6b's threshold inputs under
*Real-data findings for M6b*. M6b is built (2026-11-09), recorded under *M6b as built*. M6c–M6f are
not built yet.
Formulas were checked against the sources below on 2026-10-29; two items remain unverified (see
Consequences).

## Context

Personal API data has no Training Status or acute/chronic load (ADR 0001), so we compute our own.
The planning session found three facts in the code and spec that shape the design:

- **Pauses aren't marked.** `activity_sample.elapsed_s` counts wall-clock seconds from the first
  record, with gaps from smart recording and auto-pause. Every metric needs one gap rule.
- **Thresholds are already stored.** Activity search items in `raw_payload` carry lactate threshold
  HR and speed, FTP and Garmin's own power metrics, but `parse_activities` doesn't read them. History
  can be rebuilt by re-normalizing, with no new requests.
- **The HR zones endpoint returns today's settings only**, with no history, and isn't synced yet.

The phase 1 retro adds two constraints: upserts rewrite `updated_at` on recent rows every run
(storage contract, invariant 4), and `activity.sport` holds Garmin's unmapped type keys.

## Decision

**Milestones, one PR each:**

| PR | Scope | Migration |
|---|---|---|
| M6a | `hr_zones` dataset (one request per day), `athlete_zones` and `athlete_threshold` history, new `activity` threshold columns, `renormalize`, `thresholds set/list`, sport grouping, an `analyze` step after FIT parsing | 0006 |
| M6b | `activity_metrics` per activity | 0007 |
| M6c | `daily_load`: fitness, fatigue, form, ramp; weekly view | 0008 |
| M6d | `activity_best_effort`; curves as SQL | 0009 |
| M6e | `daily_recovery`: trends against baselines, with flags | 0010 |
| M6f | `agent` schema of views, `garminagent_agent` role, typed queries, `analyze --check`, `report` | 0011 |

M6c, M6d and M6e depend only on M6a and M6b and can be reviewed in any order.

**Prerequisites and order:**

- The owner's real-data check comes before M6a merges (first run done 2026-11-03): M6a's `renormalize` and
  the first `analyze --check` are its natural next run (ADR 0009).
- B3a (`make_blob_store`) lands before M6a touches the code next to it; B3b rebases after any open
  M6 PR (ADR 0008). B3a is a code-only seam with no AWS resources, so it may land before B1
  (owner's decision, 2026-11-03) and doesn't hold M6a behind the AWS apply.

**Storage:** plain tables written from Python; plain views for read-only rollups. Each migration
grants the app role explicitly on its new tables and extends `tests/storage/test_privileges.py` and
the schema guard test (storage contract, invariant 7), rather than relying on 0001's default
privileges.

**Recompute:**

- **Per-activity metrics** store an input fingerprint: a hash of the activity fields they use, the
  thresholds in effect on that date, the FIT `parser_version` and `algo_version`. A row is recomputed
  only when it is missing or its fingerprint differs. `updated_at` is not used, because every sync
  rewrites it for the last 7 days of activities. At most `ANALYTICS_MAX_ACTIVITIES` per run, so the
  first catch-up spreads over runs like the backfill; `analyze --full` has no cap.
- **Daily series** (`daily_load`, `daily_recovery`) recompute fully each run (about 730 rows a year).
- **Days** come from `start_time_local`, falling back to UTC; an activity crossing midnight counts on
  its start day.
- **Gaps:** a sample covers time until the next one, up to `MAX_GAP_S` = **10 s**; within that, its
  values carry forward to 1 s resolution before rolling windows. A longer gap is a pause: it counts
  toward no total and breaks rolling windows. Set from the real-data histogram (2026-11-03): gaps of
  up to 10 s (smart recording) hold about 98 % of recorded time and stop sharply after 10 s, while
  longer gaps are rare pauses. The first proposal, 5 s, would have treated about a third of
  recorded time as paused.
- **Sport groups** from Garmin's type keys: run (`running`, `trail_running`), ride (`cycling`, `gravel_cycling`,
  `road_biking`, `mountain_biking`), ski (`skate_skiing_ws`, `cross_country_skiing_ws`), strength
  (`strength_training`), and other for any key not yet mapped (counted, so new keys get noticed).
  Every group counts toward fitness and fatigue; loads are also reported per group.
- **Analytics failures never fail the sync**; counts only go to `sync_run.counts["analytics"]`.

**Metrics** (neutral names, since Normalized Power, Intensity Factor and TSS are Peaksware trademarks):

| Metric | Definition |
|---|---|
| Weighted power | 30 s rolling mean, 4th power, mean, 4th root; from second 30; windows don't cross pauses |
| Power load | `s × WP × I / (FTP × 3600) × 100`, `I = WP/FTP`; 1 h at FTP = 100 |
| HR load | Per-sample Banister TRIMP `Δt·x·0.64·e^(b·x)`, x = HR reserve fraction, scaled so 1 h at LTHR = 100. Our own written definition; `b` is a required setting (below) |
| Chosen load | Power load if available; else HR load with ≥50% HR coverage; else none, never a guess |
| Fitness, fatigue, form | `CTL_t = CTL_{t-1} + (L_t − CTL_{t-1})/42`, ATL the same with 7; form = previous day's CTL − ATL |
| Zones | Garmin's per-sport HR floors valid on the date; Coggan's 7 power levels (% of FTP); a 3-zone split |
| Decoupling | `1 − EF₂/EF₁`, EF = speed or power ÷ HR, halves by moving time, steady sessions ≥60 min, first 10 min excluded |
| Best efforts | Shortest time per distance (400 m–marathon) for run and ride first; max rolling mean power per window (5 s–5 h) where power exists |
| Recovery | 7-day mean of ln(HRV) against a 60-day baseline ±0.5 SD, resting HR the same way; flags are trends, not diagnoses. Weight excluded |

**Traceability:** each metrics row records the thresholds and `algo_version` it used. Threshold
precedence: manual, then Garmin zones, then activity values; a value holds until the next change.

**Owner's answers (2026-11-03):**

- **HR load coefficient is a required choice in config.** `ANALYTICS_TRIMP_B` accepts exactly
  `1.92` or `1.67` (the published coefficients for men and for women) and has no default; any other
  value fails settings validation. While it is unset, HR load isn't computed: activities fall back
  to power load or to no load, `analyze` counts them as `hr_load_not_configured`, and `analyze` and
  `status` warn. The sync never fails over it. The value implies sex, so it stays in configuration:
  it isn't stored as profile data, and never appears in agent views, tool output or logs; metrics
  rows include it only in their input fingerprint.
- **The agent role is created in M6f** (also confirmed in the AWS session). Its name
  (`garminagent_agent`) and grants are defined once, in a `garminagent.storage` module that migration
  0011 uses to grant SELECT on the `agent` schema only, when the role exists (the 0001 pattern). The
  login role itself is created where its password lives: the compose init script for new local
  volumes, a documented one-off command for existing ones, and B3b's `db-bootstrap` on AWS, which
  reuses the same module (ADR 0008). The agent layer connects through a third URL setting,
  `AGENT_DATABASE_URL`, handled like the other two: `SecretStr`, password hidden in `config` output,
  never logged; on AWS a third Secrets Manager secret from which `db-bootstrap` reads the password.
- **Running load is HR-only in phase 2.**
- **Strength sessions count toward fitness and fatigue**, as their own sport group so their share
  stays visible; their HR load understates muscular strain.
- **The weight-unit check runs with the real-data check** (`scripts/real_data_check.py`), before M6a's
  first `renormalize` and `analyze --check`.

## M6a as built (2026-11-07)

Choices made where the decision above left room. They change no milestone, table or metric
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
  summaries carry it on every synced day. Decided below.
- **History before the first zones fetch has no max HR**, and LTHR only for runs from the first
  detection on. Without more input, HR load would be "none" for nearly all of the two-year history,
  and fitness needs 126 days of it (see *Consequences*). Decided below.
- **Ski power load needs a ski FTP set by hand** (`thresholds set ftp … --sport ski`). Without one,
  ski sessions use HR load like everything else.

**Owner's answers (2026-11-07)**, which close former OPEN_QUESTIONS 36 and 39:

- **Resting HR for HR load comes from the daily summaries.** The resolver takes the mean of
  `daily_summary.resting_hr` over the 7 days ending the day before the activity. It is the
  lowest-precedence source, after manual, Garmin zones and activity values. It is computed when
  resolving, not stored as timeline rows, and each metrics row records the value it used. With
  fewer than 3 days in the window there is no resting HR, so that session gets no HR load, never a
  guess. The day before, because the activity day's summary is still partial when a morning
  session syncs.
- **The first known value also holds before it was first seen.** For a date before any row of a
  type, the resolver uses that type's earliest row for the group, else for `all`. This backward
  fill has the lowest precedence, so it never hides a dated value. In practice, history before the
  first zones fetch uses that fetch's max HR and LTHR, and run LTHR before the first detection uses
  the first detection. Metrics rows that used a backward-filled value carry `thresholds_backfilled`,
  so agents can tell older load is approximate. History isn't filled by hand: a manual value
  outranks Garmin zones on every later date, so it would freeze over later Garmin updates.
- **Activities deleted or merged at Garmin stay in the local tables; there is no reconciliation.**
  The owner practically never deletes activities, so phantom load is negligible. Revisit if that
  changes (phase 1 review L10).
- **`ANALYTICS_TRIMP_B` is set** in the owner's `.env` (2026-11-07). Its value stays out of the
  repository, as decided on 2026-11-03.

## M6b as built (2026-11-09)

`activity_metrics` (migration 0007) holds one row per activity, written by the `analyze` step's
third part (`garminagent.analytics.activity_metrics`, formulas in `analytics/metrics.py`).
Choices made where the decision above left room; they change no milestone or table.

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
  Garmin (decided above).

## Alternatives considered

- **Materialized views.** Rejected: harder to unit test; exponential averages are awkward in SQL; and
  refreshing needs ownership or `MAINTAIN` (PostgreSQL 17+), widening the DML-only app role.
- **Recompute on `updated_at`.** Rejected: it means "last written", so recent activities would be
  recomputed every run.
- **Pace-based running load now.** Deferred: Minetti coefficients differ between secondary sources
  and need checking against the paper. Running uses HR load in phase 2.
- **Acute:chronic workload ratio.** Not a phase 2 metric (Impellizzeri et al. 2020). ADR 0007 listed
  it as a candidate safety limit; phase 3 decides its limits from documented sources, and computes
  the ratio there only if a chosen limit needs it (OPEN_QUESTIONS 11).
- **Garmin's `training_load` as the load.** Kept for reference only: EPOC-based, different scale.
- **numpy.** Not until measurements justify a new dependency.

## Consequences

- On real data (2026-11-03) every activity has HR, but power exists only in most ski sessions and in
  no ride, and Garmin's FTP is a cycling value. HR load is therefore the load for practically every
  activity, so `ANALYTICS_TRIMP_B` must be set before M6b's results mean anything. Power load,
  weighted power and power levels stay small in M6b, ready for a power meter, but currently apply
  to no activity.
- HR load under-scores short hard intervals, because HR responds slowly. Agents must not read it as
  equal to power load for such sessions.
- Fitness needs about 126 days (3×42) of history to be meaningful; rows record when it is reliable.
- Decoupling and HR load choices are ours and must be documented, because no vendor publishes theirs.
- M6f makes the agent boundary a database rule. The AWS bootstrap (B3b) must create the same role.
  See the [agent layer contract](../docs/contracts/agent-layer.md).
- Metric correctness is a training-safety issue: hand-computed test values (for example, a 100/300 W
  30 s square wave has weighted power ≈ 221.8 W; a constant daily load of 100 gives fitness ≈ 63.65
  after 42 days), plus `analyze --check` against Garmin's own numbers on real data, printing counts
  only.
- Unverified: the Minetti coefficients (OPEN_QUESTIONS 30) and the AIS formula for the HRV smallest
  worthwhile change (OPEN_QUESTIONS 31).

## Sources (checked 2026-10-29)

- TrainingPeaks, [Normalized Power, Intensity Factor and TSS](https://www.trainingpeaks.com/learn/articles/normalized-power-intensity-factor-training-stress/) (IF definition, trademarks); [MathWorks Cody 3064](https://www.mathworks.com/matlabcentral/cody/problems/3064) (the Allen & Coggan NP steps); [Rouvy](https://support.rouvy.com/hc/en-us/articles/4481820359313) (TSS formula)
- [Performance Management Chart](https://support.myprocoach.net/hc/en-us/articles/360040589772) (CTL/ATL update and time constants)
- TrainingPeaks, [Aerobic endurance and decoupling](https://www.trainingpeaks.com/learn/articles/aerobic-endurance-and-decoupling/) (thresholds); [intervals.icu forum](https://forum.intervals.icu/t/efficiency-factor/38610) (EF-based formula)
- Suunto, [TSS and hrTSS](https://www.suunto.com/en-us/sports/News-Articles-container-page/training-with-tss-and-hrtss) (HR load limits); [Garmin forum](https://forums.garmin.com/developer/connect-iq/f/app-ideas/6776/data-field-trimp---training-impulse) (TRIMP coefficients)
- [Stages: Coggan power levels](https://manuals.stagescycling.com/en/dash-l10/l10-user-manual/training-zones/power-zones/)
- [AIS HRV best practice guide](https://www.ais.gov.au/__data/assets/pdf_file/0013/1130260/HRV-BPG_FINAL.pdf) (cited in search results; the PDF itself couldn't be opened); [Garmin HRV status](https://www.garmin.co.jp/minisite/garmin-technology/health-science/hrv-status)
- Impellizzeri et al. 2020, [IJSPP / PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC7534938/) (acute:chronic ratio)
- [Minetti et al. 2002](https://iris.unibs.it/retrieve/ddc633e4-3c03-4e2e-e053-3705fe0a4c80/Minetti%20JAP%202002.pdf) (to be read before grade-adjusted pace)
- [PostgreSQL predefined roles](https://www.postgresql.org/docs/19/predefined-roles.html) (`pg_maintain`)
