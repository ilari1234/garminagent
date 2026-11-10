# garminagent — guide for Claude Code

AI-assisted training planning on the user's own Garmin Connect data. Single user by design
(Garmin Personal API terms). Read first: [PROJECT_STATE.md](PROJECT_STATE.md) (status, owners,
next steps), [docs/plan.md](docs/plan.md) (decisions, milestones, data handling) and
[SECURITY.md](SECURITY.md) (threat model, controls, routines). Why things are as they are:
[decisions/](decisions/README.md). Unresolved: [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md). Layer
boundaries: [docs/contracts/](docs/contracts/README.md).

## Working across sessions

The repo is the orchestrator; no session's memory is. See
[ADR 0012](decisions/0012-repo-as-orchestrator.md).

- **Start of session:** run the `session-start` skill, or by hand: `git fetch`, then
  `git log --oneline origin/main`, `gh pr list` and `git worktree list`. These, not prose, say what merged and who owns what
  ([ADR 0016](decisions/0016-status-from-git.md)). `PROJECT_STATE.md` holds owners, blockers and
  next steps.
- **Never edit or switch branches in the main checkout**: the user and other sessions share it,
  and it's often behind `origin/main`. Work in your own worktree under `.claude/worktrees/<branch>`,
  cut from `origin/main`.
- At most two implementation sessions, one per track (A: `src/` and migrations; B: `infra/`).
  Chores (Dependabot, CVE bumps) go to a separate short session. If both tracks are taken, do only
  read-only work or ask the user.
- Any session that changes architecture, schema, contracts or conventions must add or update an
  ADR and update `PROJECT_STATE.md` in the same change. A milestone PR marks its own row Done;
  no follow-up "Record X as merged" PR.
- Each session works on its own git branch or worktree. Handoff happens through merges and PRs.
- A phase is "done" only after review by a fresh session or subagent against the plan and contracts.
  Use the `reviewer` subagent (`.claude/agents/reviewer.md`): give it the scope and file its report
  under `docs/reviews/`.
- Never put secrets, tokens or Terraform state in the repo or in chat.
- A `Proposed` ADR isn't approved: ask the user before building on it.

## Commands

```bash
uv sync                      # uv 0.13.6 exactly (required-version); Python 3.15
uv run ruff check && uv run ruff format --check
uv run pyright               # strict
uv run pytest                # Postgres tests need TEST_DATABASE_URL
docker compose up -d db      # local Postgres 18 on 127.0.0.1:5432
```

## Conventions

- Match the surrounding code: typed (pyright strict), small modules, docstrings that explain
  *why*, comments only where the code can't say it.
- Tests use fakes (`tests/garmin/fakes.py`: fake server, fake clock) and synthetic fixtures.
  No live network calls in tests or CI.
- `ruff format --check` also formats Python code blocks in Markdown files; keep doc snippets
  formatted.
- Dependencies: `uv add` (never pip); commit `pyproject.toml` and `uv.lock` together.
- uv itself is bumped by hand (see Supply chain). Close Dependabot PRs that bump the uv version or
  the uv image (as with #24).
- **Branches and pull requests only.** `main` is protected by a ruleset (no direct pushes, no
  force pushes, squash merge only, all CI checks and CodeQL required). Per task: create a branch
  (`m3-storage`, `fix/…`, `docs/…`), commit there, push it and open a PR that fills in the
  template's security checklist. Commit, push and merge only when the user asks; merge only
  with all checks green, using `gh pr merge --squash --delete-branch`.
- The repo is **public**: everything committed, every CI log and every PR comment is visible
  to anyone.

## Security rules (non-negotiable)

**Secrets and personal data**
- Never read `.env*` or anything under `data/` (raw Garmin payloads = personal health data).
  The only exception is a spike `report.json` the user points to; it holds structure only.
  Deny rules in `.claude/settings.json` (Read tool) and a PreToolUse hook
  (`scripts/claude_hooks/guard_private_paths.py`, shell command text) back this up but don't
  cover every path (e.g. scripts that open files themselves, `grep -r` from the repo root) —
  the rule applies regardless. If the hook blocks prose in a commit or PR message, pass the
  text from a file (`-F`, `--body-file`); never rephrase a command to slip past it.
- Runs against the real Garmin API are done by the user in their own terminal. Ask; don't run them.
- Secrets are `SecretStr`, unwrapped only where sent. Never log, print, `repr` or put them in
  exception messages. Errors and logs carry method, path, status — never bodies or headers.
- Test data is synthetic: fake names, coordinates 0.0/0.0, fake IDs. Never copy real values into
  tests, fixtures, docs, commit messages or chat.
- No credential-shaped literals anywhere, not even fake ones (`user:pw@host`, `PASSWORD=...`):
  build test credentials at runtime (`secrets.token_hex`, `URL.create`), and give compose and
  config no default passwords. GitGuardian and GitHub secret scanning flag them, and a
  "local-only" default is still a real password.
- When inspecting downloaded or user data is unavoidable, write a script that reports structure
  (types, counts, formats), not values.

**Supply chain**
- GitHub Actions: pin to a full commit SHA with a `# vX.Y.Z` comment; `persist-credentials:
  false` on checkout; least `permissions` per job. Check workflows with `zizmor`.
- Docker images: pin `tag@sha256:digest`. uv version changes go through `required-version`,
  the Dockerfile uv image and the `uv_build` bound together.
- Prefer tools installed from locked dependency groups or digest-pinned images over
  third-party actions. Don't use `aquasecurity/trivy-action` or `setup-trivy` (hijacked tags,
  March 2026); Trivy runs from its digest-pinned image in a job without secrets, after
  cosign verifies its signature. Bumping Trivy means updating the digest and the tag in
  `TRIVY_SIGNER` together.
- Cooldown: prefer releases at least 7 days old. A newer release is fine for a security fix;
  record it in SECURITY.md's cooldown-exception table with the reason.
- New dependency or tool: say why it's needed, check it's maintained, and read its license.
- `garmin-fit-sdk` is under Garmin's FIT Protocol License: never vendor or redistribute it,
  keep the GHCR image private, don't propose a copyleft license for this repo, and keep it
  behind `garminagent.fit` (see SECURITY.md, third-party license constraints).

**Model access (phase 3)** — follow the data-handling section in docs/plan.md: Bedrock in an
EU region with retention `none`; agents get derived metrics through logged tools; GPS, free
text, IDs and credentials never reach a model; training-safety limits are code, not prompts.

**Every milestone** ends with the security checklist in SECURITY.md, `/security-review` on the
change, and an update to SECURITY.md if the threat model changed.
