# Architecture decision records

Why the project is the way it is. One decision per file, `NNNN-short-title.md`, with Context,
Decision, Alternatives considered, Consequences and Status. Current state and next steps are in
[PROJECT_STATE.md](../PROJECT_STATE.md); unresolved items in [OPEN_QUESTIONS.md](../OPEN_QUESTIONS.md).

**Status values**

- **Accepted**: the owner decided or approved it; code or docs follow it.
- **Proposed**: a planning session recommended it; the owner hasn't approved it. Don't treat it
  as settled, and don't build on it without asking.
- **Superseded by NNNN**: kept for the reasoning; the newer record applies.

Don't rewrite an accepted record's reasoning. To change a decision, add a new record and mark the
old one superseded. Facts marked "checked on <date>" can go stale: recheck vendor facts before
relying on them. This repo is public: no personal values, account IDs or secrets in any record.

The first 13 records were written on 2026-11-01 from the planning conversation, after the fact.
Where the owner's reason wasn't stated, the record says so instead of inventing one.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-garmin-personal-api.md) | Official Garmin Personal API as the only data source | Accepted |
| [0002](0002-sync-strategy.md) | One-shot, budgeted, resumable sync with contiguous ranges | Accepted |
| [0003](0003-storage-and-data-model.md) | PostgreSQL, raw plus normalized layers, blob store | Accepted |
| [0004](0004-fit-parsing-sdk.md) | `garmin-fit-sdk` for FIT parsing, behind one module | Accepted |
| [0005](0005-language-and-tooling.md) | Python 3.15, uv, httpx, strict typing | Accepted |
| [0006](0006-model-access-and-data-handling.md) | Bedrock in the EU, retention `none`, derived metrics only | Accepted; one wording correction pending |
| [0007](0007-agent-plan-write-boundaries.md) | How agents may create or change plans | Principles accepted; design open |
| [0008](0008-cloud-deployment.md) | AWS via Terraform, staged B0–B3 | Accepted |
| [0009](0009-phases-and-boundaries.md) | Phases, tracks and what each may depend on | Accepted |
| [0010](0010-public-repository.md) | Public repository and what stayed in history | Accepted |
| [0011](0011-supply-chain-controls.md) | Pinning, scanners and cooldown | Accepted |
| [0012](0012-repo-as-orchestrator.md) | The repo, not a session, holds project state | Accepted |
| [0013](0013-analytics-design.md) | Phase 2 metric definitions and storage | Proposed |
