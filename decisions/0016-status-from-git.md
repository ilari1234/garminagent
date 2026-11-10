# 0016: Status that git holds is read from git

**Status:** Accepted (owner, 2026-11-10). Refines [0012](0012-repo-as-orchestrator.md).

## Context

ADR 0012 made `PROJECT_STATE.md` the single source of truth for where the project stands. In
practice it also copied facts that git and GitHub already hold: which PRs merged, their numbers,
the migration range, what each milestone built. The same milestone appeared up to 13 times
(status paragraph, table, next steps, "what exists"). Each merge then needed a follow-up "Record X
as merged" PR, and those were about half of recent PRs (#26, #30, #31, #33, #35). Copies drifted:
on 2026-11-10 the file and a coordinating session's briefing both described M6b as in review and
no PRs open after #34 had merged and #35 was open. Stale context is worse than none, because
sessions act on it.

## Decision

- **Derivable facts are read, not written.** Merged work, PR numbers, open PRs, worktrees and the
  next migration number come from `git log origin/main`, `gh pr list`, `git worktree list` and the
  migrations directory. `PROJECT_STATE.md` lists those commands.
- **`PROJECT_STATE.md` holds what git can't:** owners, dependencies, blockers, decisions and
  actions waiting on the owner, and the order of next steps.
- **Each fact is written once.** One row per piece of work; next steps point at rows. Detail
  (what was built, choices made) lives in the ADR's *as built* section, not in the state file.
- **A PR marks its own row Done** before merging. `main` sees the change only after the merge, so
  no follow-up record PR is needed.
- **Next steps list open items only.** Finished items are removed, not struck through; git history
  keeps them.

## Alternatives considered

- **Keep copying status, but be more careful.** Rejected: drift came from duplication, not care.
- **Generate the status table from GitHub in CI.** Rejected for now: more machinery than a short
  table needs, and owners and blockers still need writing by hand.
- **A coordinating session that briefs others.** Already rejected in ADR 0012; its briefing went
  stale within an hour.

## Consequences

- A session runs three commands at start instead of trusting prose about merges.
- Fewer PRs that only update `PROJECT_STATE.md`, so less owner review time per milestone.
- The state file can't answer "what PR did X" on its own; `git log --grep` or `gh pr list --search`
  can.
