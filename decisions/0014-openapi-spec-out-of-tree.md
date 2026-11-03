# 0014: Garmin's OpenAPI spec stays out of the tree

**Status:** Accepted (owner's decision, 2026-11-03). Settles OPEN_QUESTIONS 21; amends what ADR 0010
left published.

## Context

`docs/garmin-personal-api/openapi.json`, Garmin's spec for the Personal API, was committed while the
repo was private. When the repo went public (ADR 0010), the owner chose to publish history as is,
and the right to redistribute the spec was recorded as unverified.

## Decision

Remove the spec from the tree and gitignore its path. Developers download it from Garmin and keep a
local copy at the same path. History isn't rewritten.

## Alternatives considered

- **Check Garmin's terms and keep it if allowed.** Not chosen: removing it costs little, and the
  project doesn't need to publish it.
- **Rewrite history as well.** Not chosen: the owner declined history rewrites in ADR 0010, and
  copies of a public repo may already exist.

## Consequences

- The spec stays visible in history before this change.
- What the project relies on from the spec is kept in our own words: the
  [evaluation](../docs/garmin-personal-api/evaluation.md), the client's endpoint table and
  normalization models, and the ADRs that cite field names.
- Sessions that need the spec read the owner's local, gitignored copy, which holds no personal data.
