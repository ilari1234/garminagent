---
name: milestone-close
description: Close a garminagent milestone or stage (M6c, B1, …) before its PR is opened or merged. Runs the checks, the SECURITY.md checklist, /security-review, the build notes and the PROJECT_STATE update in the same PR, so no follow-up "Record X" PR is needed. Use when the user says a milestone is ready, asks to open its PR, or asks "what's left before merge".
---

# Milestone close

One PR carries the milestone and everything that records it (ADR 0016, ADR 0017). Work through
these in order, in the milestone's own worktree. Stop and tell the user at the first item that
fails; don't open the PR around it.

## 1. Checks pass locally

```bash
uv run ruff check && uv run ruff format --check
uv run pyright
uv run pytest            # with TEST_DATABASE_URL set, so Postgres tests run too
```

Track B also: `fmt`, `init -backend=false -lockfile=readonly` and `test` in each changed root,
through the digest-pinned Terraform image, as in `infra/README.md` (*Running the checks
locally*). CI runs the rest (TFLint, Trivy). Never `plan`, `apply` or touch state.

Report which tests didn't run and why.

## 2. The spec still matches

- Every change to architecture, schema, contracts or conventions has an ADR added or updated
  (a new record if it changes an accepted decision).
- Touched contracts in `docs/contracts/` describe what the code now does.
- Owner decisions made during the milestone are in the ADR. Implementation choices are in the build
  notes (step 4).

## 3. Security

- Answer SECURITY.md's *Security in every milestone* checklist from the code, one line each.
- Track A, new tables: grants in the same migration (`tests/storage/test_privileges.py`), the
  schema guard and the leak test cover them, and logs and `counts` carry counts only.
- Track B: the stage's row in `docs/aws-deployment.md`, *Security review per stage*.
- Run `/security-review` on the change. Fix findings, or record accepted ones in SECURITY.md under a
  `### <milestone> review (<date>)` heading, as M6a and M6b did.
- Update SECURITY.md if the threat model changed.

## 4. Build notes

Add a `## <Milestone> as built (<date>)` section to `docs/build-notes/analytics.md` (track A) or
`docs/build-notes/aws.md` (track B): what was built, choices made where the ADR left room, any
real-data findings (counts only), and what later work must provide.

## 5. PROJECT_STATE, in this PR

- Mark the milestone's row **Done** with a link to its build notes section. `main` only sees it
  after the merge, so there's no follow-up PR.
- Update the rows and next steps this milestone unblocks. Remove finished next steps; don't
  strike them through.
- Add owner actions the milestone creates (a terminal run, an apply) as next steps.

## 6. Review

- For the last milestone of a phase, or when the user asks: run the `reviewer` subagent on the
  branch and file its report as `docs/reviews/<scope>-review.md`. A phase is done only with no open
  high finding.

## 7. The PR

- **Decision 1:** ask the user before committing, pushing and opening the PR.
- Write the commit message and PR body to files (Write tool) and pass them with `-F` and
  `--body-file`; the hook blocks some Markdown in heredocs.
- Fill in the PR template's *Project state* and *Security checklist* with answers, not just ticks.
- Watch the checks in the background (`gh pr checks <n> --watch`) and report as soon as they
  finish: all green, or which check failed and why. The user shouldn't have to poll.
- **Decision 2:** ask whether to merge. Merge only on their yes, with every required check green:
  `gh pr merge <n> --squash --delete-branch`.
- After the merge, move out of the worktree, then remove it and the local branch
  (`git worktree remove …`, `git branch -D …`).
