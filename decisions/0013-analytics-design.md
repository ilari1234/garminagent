# 0013: Phase 2 metric definitions and storage

**Status:** Proposed. The phase 2 planning session produced this on 2026-10-29; the owner hasn't
approved it and has four questions to answer (OPEN_QUESTIONS). Nothing is built. Formulas and sources
were not re-checked when this record was written.

## Context

Personal API data has no Training Status or acute/chronic load (ADR 0001), so we compute our own.
The planning session found three facts in the code and spec that shape the design:

- **Pauses aren't marked.** `activity_sample.elapsed_s` counts wall-clock seconds from the first
  record, with gaps from smart recording and auto-pause. Every metric needs one gap rule.
- **Thresholds are already stored.** Activity search items in `raw_payload` carry lactate threshold
  HR and speed, FTP and Garmin's own power metrics, but `parse_activities` doesn't read them. History
  can be rebuilt by re-normalizing, with no new requests.
- **The HR zones endpoint returns today's settings only**, with no history, and isn't synced yet.

## Decision (proposed)

**Milestones, one PR each:**

| PR | Scope | Migration |
|---|---|---|
| M6a | `hr_zones` dataset, `athlete_zones` and `athlete_threshold` history, new `activity` threshold columns, `renormalize`, `thresholds set/list`, an `analyze` step after FIT parsing | 0006 |
| M6b | `activity_metrics` per activity | 0007 |
| M6c | `daily_load`: fitness, fatigue, form, ramp; weekly view | 0008 |
| M6d | `activity_best_effort`; curves as SQL | 0009 |
| M6e | `daily_recovery`: trends against baselines, with flags | 0010 |
| M6f | `agent` schema of views, `garminagent_agent` role, typed queries, `analyze --check`, `report` | 0011 |

**Storage:** plain tables written from Python; plain views for read-only rollups. Per-activity metrics
recompute only when an input changed, capped per run; daily series recompute fully.

**Metrics** (neutral names, since Normalized Power, Intensity Factor and TSS are Peaksware trademarks):

| Metric | Definition |
|---|---|
| Weighted power | 30 s rolling mean, 4th power, mean, 4th root; windows don't cross pauses |
| Power load | `s × WP × I / (FTP × 3600) × 100`, `I = WP/FTP` |
| HR load | Per-sample Banister TRIMP, scaled so 1 h at LTHR = 100. Our own written definition |
| Chosen load | Power load if available; else HR load with ≥50% HR coverage; else none, never a guess |
| Fitness, fatigue, form | Exponential averages over 42 and 7 days; form = previous day's difference |
| Zones | Garmin's per-sport HR floors valid on the date; Coggan's 7 power levels; a 3-zone split |
| Decoupling | `1 − EF₂/EF₁`, steady sessions ≥60 min, first 10 min excluded |
| Best efforts | Max rolling mean power per window; shortest time per distance |
| Recovery | 7-day mean of ln(HRV) against a 60-day baseline ±0.5 SD; flags are trends, not diagnoses |

**Traceability:** each metrics row records the thresholds and `algo_version` it used. Threshold
precedence: manual, then Garmin zones, then activity values.

## Alternatives considered

- **Materialized views.** Rejected: harder to unit test; exponential averages are awkward in SQL; and
  refreshing needs ownership or `MAINTAIN`, widening the DML-only app role.
- **Pace-based running load now.** Deferred: Minetti coefficients differ between secondary sources
  and need checking against the paper. Running uses HR load in phase 2.
- **Acute:chronic workload ratio.** Left out on purpose (Impellizzeri et al. 2020). Conflicts with an
  early safety-limit idea (ADR 0007).
- **Garmin's `training_load` as the load.** Kept for reference only: EPOC-based, different scale.
- **numpy.** Not until measurements justify a new dependency.

## Consequences

- HR load under-scores short hard intervals, because HR responds slowly. Agents must not read it as
  equal to power load for such sessions.
- Fitness needs about 126 days (3×42) of history to be meaningful; rows record when it is reliable.
- Decoupling and HR load choices are ours and must be documented, because no vendor publishes theirs.
- M6f makes the agent boundary a database rule. See the [agent layer contract](../docs/contracts/agent-layer.md).
- Metric correctness is a training-safety issue: hand-computed test values, plus `analyze --check`
  against Garmin's own numbers on real data, printing counts only.
