# Project state

**What git can't tell you:** owners, blockers, decisions waiting on the owner, and what comes next.
Last updated 2026-11-12. Facts that git and GitHub already hold are read from them, not copied here
([ADR 0016](decisions/0016-status-from-git.md)):

| Question | Ask |
|---|---|
| What merged, and when? | `git fetch && git log --oneline origin/main` |
| Which PRs are open, and who owns them? | `gh pr list` (branch names name the milestone) |
| Which sessions have worktrees? | `git worktree list` |
| What's the next migration number? | `ls src/garminagent/storage/migrations/versions/` (track A only) |

Each fact below is written once. Detail lives in the linked ADR, not in this file. A PR marks its
own row **Done** before it merges, because `main` only sees the change after the merge, so no
"Record X as merged" follow-up is needed.

Goal: AI agents that create, update and evaluate training plans (off-season, half marathon, marathon,
ultra, road and gravel cycling) from the owner's Garmin Connect data. Single user by design.

## Status

| Work | Status | Owner | Depends on | Blocked by / waits on |
|---|---|---|---|---|
| Phase 1: retrieve and store (M0–M5) | **Done** 2026-11-04 ([review](docs/reviews/phase1-review.md), [ADR 0015](decisions/0015-sync-failure-handling.md)). Low findings L1–L11 open (OPEN_QUESTIONS 34) | | | |
| Real-data check | **Done** 2026-11-03 | Owner's terminal | Phase 1 | |
| Phase 2 design | **Accepted** 2026-11-03 ([ADR 0013](decisions/0013-analytics-design.md)) | | | |
| M6a thresholds, zones, `analyze` | **Done** ([build notes](docs/build-notes/analytics.md#m6a-as-built-2026-11-07)) | | | |
| M6b per-activity metrics | **Done** ([build notes](docs/build-notes/analytics.md#m6b-as-built-2026-11-09)) | | M6a | |
| M6c–M6e | **Next**, any order | Track A | M6b | OPEN_QUESTIONS 40 (lab test input), 41 (decoupling steadiness) |
| M6f | Planned (ADR 0013) | Track A | | |
| AWS plan | **Accepted** ([ADR 0008](decisions/0008-cloud-deployment.md), [docs/aws-deployment.md](docs/aws-deployment.md)) | | | |
| AWS B0 tooling | **Done**; its `infra-*` checks are required on `main` | | | |
| AWS B1 foundation | **Done** (code; [build notes](docs/build-notes/aws.md#b1-implementation-2026-11-09), [review](docs/reviews/b1-review.md)) | Owner's terminal | B0 | Owner applies by [docs/aws-b1-checklist.md](docs/aws-b1-checklist.md) |
| AWS B2 Bedrock | **Next** | Track B | B1 | B1 applied; owner applies; no real prompt before its retention verification |
| AWS B3a blob store seam | **Done** | | | |
| AWS B3b app code | **Done** ([build notes](docs/build-notes/aws.md#b3b-implementation-2026-11-07): what B3c/B3d must provide) | | B3a | |
| AWS B3c–d workload | Planned | Track B | B1, B3b; B3c after OPEN_QUESTIONS 8 | Owner applies |
| Phase 3: agents | **Not started**; principles only ([ADR 0007](decisions/0007-agent-plan-write-boundaries.md)) | | M6 core, M6f, B2 | Design: OPEN_QUESTIONS 10–18 |
| Phase 4: delivery | **Outline only** | | Phase 3; hosting depends on B3 | |

**Parallel work** ([ADR 0012](decisions/0012-repo-as-orchestrator.md)): at most two implementation
sessions at once, one per track, each on its own branch or worktree. **Track A (application)** alone
adds migrations. **Track B (infrastructure)** alone touches `infra/`; exceptions are in ADR 0008.
Chores (Dependabot, CVE bumps) go to a separate short session.

## Next steps

Open items only. Each one points at its row above instead of restating it.

1. **Track A:** M6c–M6e, in any order. Each ends with a `reviewer` subagent review.
2. **Owner, own terminal:** apply B1 by [docs/aws-b1-checklist.md](docs/aws-b1-checklist.md)
   (console steps, `org` and `account` applies, state migration, section 8 checks including firing
   the budget action once), then record the checks as yes/no in SECURITY.md's B1 review.
3. **Track B:** B2 once B1 is applied (its code can start before). B3c/B3d must pass
   `ANALYTICS_TRIMP_B` to the tasks, or HR load stays off on AWS.
4. **Phase 3 design:** a fresh, read-only planning session can start now, alongside M6c (M6b's
   numbers are confirmed on real data). It settles OPEN_QUESTIONS 11 first (ADR 0007 and ADR 0013
   disagree on the acute:chronic ratio), before any limit numbers, then the rest of 10–18.
5. **Before phase 3 is built:** settle OPEN_QUESTIONS 10–18, and pass B2's retention verification
   before any real prompt.

## What exists today

What the code does is in the [README](README.md) and [docs/contracts/](docs/contracts/README.md);
this section lists only what the tree doesn't show.

- **Runtime:** local Docker only (`db`, `migrate`, `scheduler`, plus `app` in the `manual` profile).
- **CI on `main`:** lint, test, pip-audit, zizmor, cosign and Trivy scan, image to private GHCR,
  CodeQL, and the `infra-*` checks (all without AWS credentials).
- **Infra:** `infra/org` and `infra/account` hold B1's resources, applied by the owner after merge; `infra/workload` has none until B3c.
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
and its row in this file marked **Done** in the same PR. **A phase:** additionally reviewed by a
fresh session or subagent against the plan and contracts (the `reviewer` subagent,
`.claude/agents/reviewer.md`), with the result linked from its row.
