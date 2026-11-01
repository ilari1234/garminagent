# 0009: Phases, tracks and what each may depend on

**Status:** Accepted (phases from the original planning; two tracks agreed 2026-10-29).

## Context

The project spans data retrieval, analytics, agents, delivery and a cloud move. Without boundaries,
agents would be built on unverified data, or cloud costs would start before they're needed.

## Decision

| Phase | Scope | Explicitly not in it |
|---|---|---|
| 1 Retrieve and store | Client, storage, sync, FIT parsing, scheduling | Metrics, models, cloud |
| 2 Analytics | Load, fitness and fatigue, zones, curves, recovery trends, agent read layer | Any model call, plans |
| 3 Agents | Planner, evaluator, adjuster; plan storage; safety limits | UI, watch export |
| 4 Delivery | UI, plan calendar, FIT workout export | |
| AWS (track B) | B0–B3, parallel to the above | |

**Dependency order**

- A real-data check comes before relying on M6 results.
- B1 precedes B2. B2 works without B3.
- Phase 3 needs core M6 and B2, but not B3.
- B3 needs B1 and is easier once the schema settles.
- Phase 4 follows phase 3; its hosting depends on B3.
- M4 was split into a/b/c to keep PRs reviewable. Later milestones follow the same habit.

**Only code crosses boundaries through the contracts** in [docs/contracts/](../docs/contracts/).
Ingestion may read raw data; analytics reads normalized data; agents read only the agent layer.

## Alternatives considered

- **Move to AWS first.** Rejected: pays for RDS and Fargate before anything needs them.
- **Agents before analytics, feeding them normalized data directly.** Rejected: the rule is derived
  metrics only, and the safety limits need trusted load numbers.
- **One long session or one track.** Rejected: see ADR 0012.

## Consequences

- Phase 3 can run from the owner's machine against Bedrock while data stays local.
- Plans stored in phase 3 make rollback from AWS harder: after that, a rollback needs a data dump.
- "Done" now includes an independent review (ADR 0012). Phase 1's code is merged, but that review
  hasn't happened yet.
