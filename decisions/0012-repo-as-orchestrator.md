# 0012: The repo, not a session, holds project state

**Status:** Accepted (owner's brief, 2026-11-01). Refined by [0016](0016-status-from-git.md):
facts git holds are read from git, not copied into `PROJECT_STATE.md`.

## Context

Phase 1 and its planning happened in one long Claude Code session, which held decisions nobody had
written down. It ran out of context as phase 2 and AWS planning began. A session is disposable; its
reasoning must not be.

## Decision

- **State lives in files:** `PROJECT_STATE.md` (where things stand), `decisions/` (why),
  `OPEN_QUESTIONS.md` (what isn't settled), `docs/contracts/` (interfaces).
- **Changes to architecture, schema, contracts or conventions** add or update an ADR and update
  `PROJECT_STATE.md` in the same change.
- **Each session has its own branch or worktree.** Handoff happens through merges and PRs.
- **A phase is done** only after review by a fresh session or subagent against the plan and contracts.
- **Two tracks, at most two active sessions:** A (application) and B (infrastructure). The owner's
  review time is the real limit.
- **One session per phase or stage.** Start a fresh one when context gets tight.
- **Ownership:** only track A adds migrations, since the chain is linear. Only track B touches
  `infra/`. Each track edits only its own sections of shared docs. Exceptions are listed in ADR 0008.
- **Small PRs, merged often.**

## Alternatives considered

- **One session doing everything.** Rejected: context ran out, and work stalled behind it.
- **A coordinating session that stays open.** Rejected as the long-term model: it keeps state in a
  place other sessions can't read. This ADR's brief is the end of that session's role.
- **Three or more parallel sessions.** Rejected: a third would mostly queue PRs for review.

## Consequences

- A fresh session can start with only the repo. The first 13 ADRs were written after the fact, so
  some reasons are reconstructed; they say so where the owner's reason isn't known.
- Plans held in planning sessions aren't in the repo until committed. ADRs 0008 and 0013 hold their
  essentials until those sessions land their own docs.
- Slightly more writing per PR, in exchange for no dependence on a session's memory.
