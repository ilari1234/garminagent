# 0011: Pinning, scanners and cooldown

**Status:** Accepted (owner, 2026-10-25). Details: SECURITY.md, CLAUDE.md.

## Context

The owner wanted security handled early, while it's cheap. The year's supply-chain incidents
(hijacked Trivy action tags in March 2026, the eMudhra CA incident in October 2026) are real
threats to a repo whose CI builds an image holding health-data access code.

## Decision

- Actions pinned to full commit SHAs; images pinned by digest; locked dependencies with hashes.
- pip-audit, zizmor, Trivy, CodeQL and ruff bandit rules, gating the image build.
- **Trivy from a digest-pinned image**, verified with cosign, in a job without secrets. Not
  `trivy-action` or `setup-trivy`.
- **7-day cooldown** on new releases; exceptions recorded in SECURITY.md with a reason.
- Prefer tools from locked dependency groups or pinned images over third-party actions.

## Alternatives considered

- **Replacing Trivy after the action hijack.** Rejected: the hijack hit the actions, not the digest
  pinned image, and the owner kept it.
- **Dropping cosign.** The owner questioned its value. Kept: modest value, but automatic and cheap.
- **Making GitGuardian a required check.** Left advisory; it found real issues but is noisy on
  patterns such as `${VAR:?}` in URLs. Whether it's worth keeping long term was discussed, not closed.

## Consequences

- Monthly manual upkeep: `uv lock --upgrade`, Trivy and cosign image bumps (Dependabot doesn't track
  those). Dependabot's Docker updater ignored the cooldown once, so check release age.
- A Trivy bump updates the digest and the tag in `TRIVY_SIGNER` together.
- Trivy findings forced pip out of the runtime image; cosign 3.1.3 trusted a distrusted root, so
  3.1.4 is used.
- CodeQL alerts #1 and #2 (logging the key's expiry date) were dismissed as false positives.
- Owner-side settings that aren't in git: see OPEN_QUESTIONS.
