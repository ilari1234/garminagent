# Project state

**The single source of truth for where the project stands.** Last updated 2026-11-04. Update this file
in any PR that changes status, architecture, schema, contracts or conventions (ADR 0012).

Goal: AI agents that create, update and evaluate training plans (off-season, half marathon, marathon,
ultra, road and gravel cycling) from the owner's Garmin Connect data. Single user by design.

## Status

The AWS plan is accepted (2026-11-03); B0 is next. The phase 2 plan waits on the owner's review.
No code beyond phase 1 is committed.

| Work | Status | Owner session / branch | Depends on | Blocked by |
|---|---|---|---|---|
| Phase 1: retrieve and store (M0–M5) | **Reviewed 2026-11-04, not yet "done"**: H1 and M1–M4 fixed in #19 ([ADR 0015](decisions/0015-sync-failure-handling.md)); low findings open ([review](docs/reviews/phase1-review.md)) | `main` | | A short re-check of the fixes by a fresh session or subagent (OPEN_QUESTIONS 34) |
| Real-data check | **Done** 2026-11-03, including its follow-ups | Owner's terminal | Phase 1 | |
| Phase 2: analytics (M6a–M6f) | **Accepted** 2026-11-03 ([ADR 0013](decisions/0013-analytics-design.md)); M6a next | Track A session | Phase 1 review; B3a before M6a (code-only, may precede B1) | |
| AWS plan | **Accepted** 2026-11-03 ([ADR 0008](decisions/0008-cloud-deployment.md), [docs/aws-deployment.md](docs/aws-deployment.md)) | AWS planning session; branch `docs/aws-deployment` | | Owner's merge |
| AWS B0 tooling | **Next** | Track B session | Plan merged | |
| AWS B1 foundation | Planned | Track B | B0 | Owner applies |
| AWS B2 Bedrock | Planned | Track B | B1 | Owner applies; no real prompt before its verification |
| AWS B3a–d workload | Planned | Track B | B1; B3a before M6a, B3b after open M6 PRs; B3c after the real-data check and OPEN_QUESTIONS 8 | Owner applies |
| Phase 3: agents | **Not started**; principles only ([ADR 0007](decisions/0007-agent-plan-write-boundaries.md)) | None | Core M6, M6f, B2 | Design |
| Phase 4: delivery | **Outline only** | None | Phase 3; hosting depends on B3 | |
| Repo as orchestrator (this file and friends) | **Merged** (#8) | | | |

Session IDs are local to the owner's desktop app. A session that can't open them should work from the
repo alone. The planning essentials are in the two ADRs.

**Parallel work:** phase 2 and AWS run in **separate sessions**, on separate branches or worktrees, at
most two active at once. **Track A (application)** alone adds migrations. **Track B (infrastructure)**
alone touches `infra/`. Exceptions are listed in ADR 0008.

## Next steps, in order

1. ~~**Owner:** merge the orchestrator and AWS plan PRs.~~ Done (#8, #10).
2. ~~**Fresh session or subagent:** review phase 1 against `docs/plan.md` and `docs/contracts/`.~~ Done
   2026-11-04 ([docs/reviews/phase1-review.md](docs/reviews/phase1-review.md)): 1 high, 4 medium, 11 low.
   H1 and M1–M4 are fixed in #19 (ADR 0015). Phase 1 is "done" once a short re-check confirms the fixes
   (OPEN_QUESTIONS 34).
3. ~~**Owner, own terminal:** run the real-data check.~~ Done 2026-11-03, with a second run for the follow-ups.
4. ~~**Owner:** approve the phase 2 plan.~~ ADR 0013 accepted 2026-11-03. Set `ANALYTICS_TRIMP_B` in `.env` before M6b.
5. **Track A:** M6a. **Track B:** B0, B1, B2 (B3a before M6a if the order allows).
6. **Before phase 3 starts:** settle the design items in OPEN_QUESTIONS 10–18, and pass B2's retention
   verification before any real prompt.

## What exists today

- **Code:** client, storage, sync, FIT parsing, wellness data, local scheduler, status, purge.
- **Schema:** migrations 0001–0005. Next is 0006, owned by track A.
- **Runtime:** local Docker only (`db`, `migrate`, `scheduler`, plus `app` in the `manual` profile).
- **CI on `main`:** lint, test, pip-audit, zizmor, cosign and Trivy scan, image to private GHCR, CodeQL.
- **Merged PRs:** #2–#8. Dependabot's #1 was closed.
- **Garmin's OpenAPI spec** isn't in the tree (ADR 0014); keep a local copy at the gitignored
  `docs/garmin-personal-api/openapi.json`.

## Where to find things

| Question | File |
|---|---|
| Why was it decided? | [decisions/](decisions/README.md) |
| What's unresolved? | [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) |
| What did phase 1 teach us? | [PHASE1_RETRO.md](PHASE1_RETRO.md) |
| What may each layer rely on? | [docs/contracts/](docs/contracts/README.md) |
| Plan, data handling | [docs/plan.md](docs/plan.md) |
| Threat model, routines | [SECURITY.md](SECURITY.md) |
| Running it | [docs/operations.md](docs/operations.md) |
| Rules for sessions | [CLAUDE.md](CLAUDE.md) |

## Definition of done

**A milestone:** PR merged with green checks, SECURITY.md checklist answered, `/security-review` run,
this file updated. **A phase:** additionally reviewed by a fresh session or subagent against the plan
and contracts, with the result recorded here.
