# 0013: Phase 2 metric definitions and storage

**Status:** Accepted (owner's decision, 2026-11-03). The phase 2 planning session produced this on
2026-10-29 and revised it on 2026-11-03 after the orchestrator change (#8) and two real-data runs;
the owner's answers are recorded below. M6a (2026-11-07), M6b (2026-11-09), M6c (2026-11-12),
M6d (2026-11-22) and M6e (2026-11-24) are built; what was built, choices made where this ADR left room, and the real-data findings are in
[docs/build-notes/analytics.md](../docs/build-notes/analytics.md) (ADR 0017). The owner's answers
on M6b's threshold inputs are under *Owner's decisions on M6b's inputs*, and on M6c's series under
*Owner's decisions on M6c*, on M6d's best efforts under *Owner's decisions on M6d*, and on M6e's
recovery trends under *Owner's decisions on M6e*. M6f is not built yet.
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
| M6d | `activity_best_effort`; curves as SQL (views `best_effort_curve`, `best_effort_pb`) | 0009 |
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

## Owner's decisions on M6b's inputs (2026-11-07)

Answers to what the real-data check found
([build notes](../docs/build-notes/analytics.md#real-data-findings-for-m6b-2026-11-07)). They close
former OPEN_QUESTIONS 36 and 39.

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

## Owner's decisions on M6c (2026-11-12)

Points the table above named but didn't define, answered before M6c was built.

- **Ramp is the 7-day change in fitness:** `ramp_t = CTL_t − CTL_{t−7}`, per day and sport group,
  NULL for the first 7 days of the series. It's how TrainingPeaks and most coaching sources use
  the term (their guidance quotes 4–8 points a week), and it follows from the fitness formula:
  a week's ramp is about `(week's load − 7 × CTL) / 42`. No limit is applied in phase 2; a limit
  belongs to phase 3 and needs a documented source (OPEN_QUESTIONS 11). The week-over-week change
  in weekly load stays derivable from the weekly view if a phase 3 limit needs it. Sources
  (checked 2026-11-12): TrainingPeaks,
  [Why ramp rate is an important training metric](https://www.trainingpeaks.com/learn/articles/why-ramp-rate-is-an-important-training-metric/)
  and [Understanding ramp rate](https://www.trainingpeaks.com/blog/understanding-trainingpeaks-ramp-rate-for-better-coaching/).
- **The series covers every day** from the first activity's day to the latest day with data (the
  later of the last activity and the last daily summary); rest days have load 0. No timezone
  setting decides what "today" is.
- **`all` holds the total; each sport group with an activity gets its own fitness, fatigue, form
  and ramp** over the same range, so a jump in one sport stays visible under a flat total.
- **Missing load adds 0 and is counted per day:** activities whose metrics have no load
  (`activities_unloaded`) and activities without metrics yet (`activities_pending`).
- **Reliability is recorded per row:** `history_days`, and `fitness_reliable` from 126 days
  (3 × 42), plus `thresholds_backfilled` when any load counted that day used backward-filled values.
- **The weekly view uses ISO weeks** (Monday start): summed load, sessions and moving time, and
  fitness, fatigue, form and ramp as of the week's last day.

## Owner's decisions on M6d (2026-11-22)

Points the *Best efforts* row left open, answered one at a time before M6d was built.

- **Efforts stop at pauses.** Neither a distance effort nor a power window crosses a gap over
  `MAX_GAP_S`, as for weighted power: a 20-minute effort with a café stop in it isn't one.
- **Distances and windows:** runs 400 m, 1 km, 1 mile, 5 km, 10 km, half marathon (21 097.5 m)
  and marathon; rides 10, 20, 40 and 100 km; power, in any group that has it, 5 s, 15 s, 30 s,
  1, 5, 10, 20, 30 and 60 min, and 2, 3 and 5 h. Other groups get no distance efforts.
- **Distance comes from the watch's running total** (`distance_m`), interpolated between samples
  to the metre, not from summed speed, so results match Garmin's own laps more often. A total
  that goes down (a counter reset) breaks the track like a pause and is counted.
- **A GPS spike adds no distance**: a step faster than 12.5 m/s for runs or 30 m/s for rides keeps
  its time, contributes no distance and is counted; the effort goes on across it, so an effort
  across a spike is slower than the truth, never faster. *Revised the same day after the
  real-data check:* the first rule treated a spike as a break, and with spikes in about a third
  of rides and a fifth of runs that cost roughly one 10 km effort in five (the counts are in the
  build notes, *Real-data check for M6d*). The run limit sits just above the fastest measured human sprint
  speed, 12.32 m/s (Bolt's 2009 100 m world record, from laser measurements;
  [Bioengineering 2023](https://pmc.ncbi.nlm.nih.gov/articles/PMC10669785/), checked
  2026-11-22); the ride limit above fast road descents. Both are high enough never to drop a real
  effort; no other speed filter guesses at slower distortions. Power gets no extra filter: samples
  are range-checked when stored, and real sprints reach 1500–2000 W.
- **Curves and PBs are views:** `best_effort_curve`, the best per sport group, kind and span for
  all time and for the last 365, 90 and 42 days (counted back from the day the view is read), and
  `best_effort_pb`, the all-time best with the best set before it. Both carry the activity ID so
  a person can look it up; M6f's agent views leave IDs out (docs/plan.md, data handling).

## Owner's decisions on M6e (2026-11-23 and 2026-11-24)

Points the *Recovery* row left open, answered one at a time before and during the M6e build.
The definitions live in `garminagent.analytics.daily_recovery`; any change bumps its
`algo_version`.

- **HRV is `hrv_summary.last_night_avg`**, Garmin's average over the night (ms), taken as ln(ms).
  Not the 5-minute high (one window, noisier), and not Garmin's weekly average (its own smoothing
  on top of ours). Resting HR is `daily_summary.resting_hr`, as for HR load.
- **Recent** is the mean over the 7 days ending on the day. **The baseline covers the 60 days
  before that week** (days −66 to −7), so the week being judged never pulls its own baseline
  along and a real drop shows in full. It costs a week more history before the first flag,
  which doesn't matter with two years of history.
- **Minimum days:** a recent mean needs at least 4 of its 7 days, and a baseline at least 30 of
  its 60 days; below that the value is NULL and there is no flag. HRV and resting HR are judged
  separately, and each row stores its day counts. The numbers are ours (no source gives one), a
  little stricter than resting HR for HR load (3 of 7), because a trend should rest on most of
  the week. With nights every day, the first flag comes on day 36.
- **The band is the baseline mean ±0.5 SD, where the SD is taken over the single days** in the
  baseline window (decided 2026-11-24). For one day the SD was taken over the 7-day means
  instead, following the TrainingPeaks summary below; the M6e review
  ([docs/reviews/m6e-review.md](../docs/reviews/m6e-review.md), M1) showed that this flags most
  days when nothing changes, and the owner chose single days. Measured on synthetic steady nights
  (HRV log-normal with SD 0.15, 2026-11-24): ±0.5 SD of single days flags 17–25 % of days and
  catches a sustained 10 % HRV drop within 2 to 9 days; ±0.5 SD of 7-day means flags 68–70 %;
  ±1.0 SD of 7-day means 38–41 %. These shares are for independent nights around a fixed level,
  so they are a floor: real nightly series drift with training, season and illness, and one
  night resembles the next, so their 7-day means stray further from the baseline and a higher
  share is expected. The share alone says nothing about whether recovery is good or bad
  (corrected 2026-11-24, after the owner's real-data check). With a very steady resting HR (a baseline SD under
  1 bpm), a 1 bpm change, the smallest step whole-bpm values take, can flip a flag.
- **Flags are `below`, `within` and `above`** (on the edge is `within`), per measure. Low HRV and
  high resting HR are the directions that usually go with poor recovery, but the flags name only
  the direction, and they are trends, not diagnoses. No combined flag: agents can read both, and
  combining them would be a new rule without a source.
- **Sleep, Body Battery, stress and weight stay out of M6e.** None has a trend rule in the
  *Recovery* row, and adding one would be a new metric definition without a source. Body Battery
  and stress were in docs/plan.md's first M6e outline; the owner confirmed leaving them out on
  2026-11-24 (M6e review, L1). Phase 3 design (OPEN_QUESTIONS 10–18) decides whether an agent
  needs any of them. Garmin's own HRV status stays readable in `hrv_summary`.

Source check (2026-11-23):

- TrainingPeaks' summary of a Plews-led study of cyclists
  ([New study widens HRV evidence](https://www.trainingpeaks.com/blog/new-study-widens-hrv-evidence-for-more-athletes/))
  is the only source found that states the factor: "calculate the standard deviation of the HRV
  7-day average and take 0.5 SD as the smallest worthwhile change". Its baseline was 4 weeks,
  and the rule decided each day's training intensity, where frequent signals are acceptable;
  M6e's flags are trend signals, which is why the owner chose the single-day SD. It is a
  secondary write-up, so OPEN_QUESTIONS 31 stays open.
- HRV4Training builds its normal range from "the past 60 days of your measurements"
  ([Altini, 2023](https://marcoaltini.substack.com/p/whats-your-normal-range-for-heart)) and
  publishes no width.
- The AIS guide recommends 7-day rolling averages judged against the smallest worthwhile change;
  its PDF link now redirects to a 404, so its formula is still unread.
- Schneider et al. 2019 ([Frontiers in Physiology](https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2019.00582/full))
  uses 0.2 × the between-subject SD over a 4-day baseline: a group rule that doesn't fit one
  athlete.

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
