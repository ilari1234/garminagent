# 0010: Public repository

**Status:** Accepted (owner's decision, 2026-10-25). Replaces the "private" line in the first plan.

## Context

GitHub doesn't enforce rulesets on private repositories without a paid plan. The owner wanted
enforced branch protection rather than relying on discipline.

## Decision

Make the repository public and enforce the `prevent-push-to-main` ruleset (settings: SECURITY.md).
Before publishing, the owner reviewed what history exposed and chose to **publish as is**: the
commit email, the Garmin OpenAPI spec in `docs/garmin-personal-api/` and the oldest-activity date.

## Alternatives considered

- **Stay private, enforce by convention.** Rejected: no enforcement.
- **Paid GitHub plan.** Not chosen; going public was free and also unlocked free security tooling.
- **Rewriting history first.** Declined by the owner.

## Consequences

- Everything committed, every CI log and every PR comment is visible to anyone. This shapes the rule
  of no `terraform plan` in CI (ADR 0008) and of no real values in docs.
- The right to redistribute Garmin's OpenAPI spec is **unverified** (OPEN_QUESTIONS).
- The GHCR image must stay private even though the repo is public (ADR 0004).
- There's no license file, so default copyright applies. Any license added must not be copyleft
  (ADR 0004).
- Secret scanners flag credential-shaped text, so nothing credential-shaped may be committed, even
  fake values. This was learned from repeated GitGuardian incidents during M3 (retro).
