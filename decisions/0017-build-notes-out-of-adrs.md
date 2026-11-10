# 0017: ADRs hold decisions; build notes live in docs/build-notes/

**Status:** Accepted (owner, 2026-11-10). Refines [0016](0016-status-from-git.md), which put build
detail in an ADR's *as built* section.

## Context

ADRs 0013 and 0008 grew an *as built* section per milestone: versions, implementation choices,
real-data counts, what later stages must provide. ADR 0013 reached 3.7k words, of which 2k were
build notes, and every milestone added more. Sessions read the whole record to find a decision,
and the "don't rewrite an accepted record" rule made the notes awkward to correct.

## Decision

- **An ADR holds the decision**: context, what was decided (including the owner's answers to
  questions the build raised), alternatives, consequences.
- **Build notes live per area** in `docs/build-notes/` (`analytics.md` for phase 2, `aws.md` for
  track B). Each milestone or stage PR adds its own section: what was built, choices made where the
  ADR left room, findings from real data (counts only), and what later work must provide.
- **Choice or decision?** If the owner decided it, or it changes a milestone, table, metric
  definition or contract, it goes in the ADR. If it's how the implementer filled a gap the ADR
  left open, it goes in the build notes.
- Build notes can be corrected in place; ADRs keep the existing rule (a new record to change a
  decision).

## Alternatives considered

- **Keep *as built* in the ADR.** Rejected: the records keep growing, and decisions get harder to
  find.
- **One file per milestone.** Rejected for now: per-area files keep related notes together and
  give fewer files to link.

## Consequences

- ADR 0013 went from about 3.7k to 2k words and ADR 0008 from 1.7k to 0.9k. The owner's decisions
  on M6b's inputs stay in ADR 0013, in their own section.
- Links that pointed at *M6a as built*, *M6b as built* or *B3b implementation* now point at the
  build notes.
