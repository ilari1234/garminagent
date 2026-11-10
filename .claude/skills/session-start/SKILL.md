---
name: session-start
description: Start-of-session briefing for garminagent, built from git and GitHub instead of from memory (ADR 0016). Use at the start of every session, before picking up a task, and whenever asked "where do things stand", "what's open" or "which track is free".
---

# Session start

Build the briefing from live sources every time. Don't reuse an earlier briefing or what
another session said: both go stale within hours (ADR 0016).

## 1. Gather (read-only)

Run these from any checkout; none of them changes files:

```bash
git fetch --quiet origin
git log --oneline -15 origin/main
gh pr list --state open
git worktree list
```

Then read the **Status** table and **Next steps** in `PROJECT_STATE.md` on `origin/main`
(`git show origin/main:PROJECT_STATE.md`), not the copy in the main checkout, which may be behind.

## 2. Work out who owns what

- **Track A** (application: `src/`, migrations) branches look like `m6c-…`. **Track B**
  (infrastructure: `infra/`) branches look like `infra-…` or `aws-…`. `docs/…`, `fix/…` and
  `chore/…` are short side tasks.
- A track is **taken** if an open PR or a worktree has one of its branches. A worktree whose
  branch already merged (its commit is in `origin/main`) belongs to a finished session; leave it,
  the user cleans those up.
- At most two implementation sessions run at once, one per track (ADR 0012).

## 3. Report

Reply in this shape, short, counts and names only, no real data values:

```
main: <short sha> <subject>  (<n> commits since <date of last briefing, if known>)
Open PRs: #N <title> (<branch>, checks: <state>) … or "none"
Track A: free | taken by <branch/PR>   Next: <next step from PROJECT_STATE>
Track B: free | taken by <branch/PR>   Next: <next step>
Waiting on the owner: <items from Next steps marked Owner>
Stale state: <anything PROJECT_STATE says that git contradicts, or "none">
```

## 4. Before changing anything

- Never edit or switch branches in the main checkout. Create your own worktree from
  `origin/main`:
  `git worktree add .claude/worktrees/<branch> -b <branch> origin/main`
- If both tracks are taken and the task is implementation work, stop and ask the user.
- If PROJECT_STATE contradicts git, say so in the report. Fix it only as part of a PR the user
  asked for.
