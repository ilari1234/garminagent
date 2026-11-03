# 0013: Phase 2 metric definitions and storage

**Status:** Proposed. The phase 2 planning session produced this on 2026-10-29 and revised it on
2026-11-03 after the orchestrator change (#8): a recompute rule that respects storage invariant 4,
explicit grants, ordering against the AWS track, the acute:chronic position, and prerequisites. The
owner answered its four questions on 2026-11-03 (below) but hasn't approved the record as a whole
(OPEN_QUESTIONS 4). Nothing is built.
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

## Decision (proposed)

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
  M6 PR (ADR 0008).

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
