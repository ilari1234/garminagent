---
name: session-start
description: Start-of-session briefing for garminagent, built from git and GitHub instead of from memory (ADR 0016), and set-up for a named task. Use at the start of every session, when asked to start or pick up a task ("start an M6c session", "/session-start B1"), and whenever asked "where do things stand", "what's open" or "which track is free".
argument-hint: "[task, e.g. M6c or B1]"
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

## 5. When a task is named

Task: `$ARGUMENTS` (empty means briefing only; stop after step 3). A task named in the user's
message ("start an M6c session") counts too.

1. **Map it to a track.** `M6…` (or other phase milestones) is track A, `B…` is track B. Anything
   else (docs, a fix, a chore, a planning topic) is a side task; planning and review sessions are
   read-only and don't take a track.
2. **Check the track.** If another branch holds it (open PR or worktree), stop and ask the user.
   If a worktree for *this* task already exists, it may be unfinished work from an earlier
   session: report its branch and `git -C <path> status --short` counts, and ask whether to
   continue there. Never start a second copy.
3. **Read for the task**: the track's entry doc (`docs/tracks/application.md` or
   `docs/tracks/infrastructure.md`), the task's part of its spec (ADR 0013 for M6, the stage's
   section of `docs/aws-deployment.md` for B), the build notes it builds on, and the
   OPEN_QUESTIONS items it touches.
4. **Create the worktree** from `origin/main`, branch named after the task:
   `git worktree add .claude/worktrees/<branch> -b <branch> origin/main` with `<branch>` like
   `m6c-<topic>` or `infra-b2`. Work only there from now on.
5. **Report a plan**: what the task delivers according to the spec, the files and tables it
   touches, open questions that block or shape it, what the owner must do or decide, and how it
   will be checked. Then ask before writing code.
