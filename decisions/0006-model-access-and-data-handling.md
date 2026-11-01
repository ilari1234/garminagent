# 0006: Bedrock in the EU, retention `none`, derived metrics only

**Status:** Accepted (owner asked for this to be recorded after the 2026-10-25 discussion). Not
implemented yet. One wording correction is pending; see Consequences.

## Context

The data is GDPR special-category health data. It already sits at Garmin and at services synced
from it, but the owner asked how sending it to Anthropic's models should be judged. The goal is to
add as few parties and as little exposure as possible.

## Decision

The full rules are in [docs/plan.md, Data handling](../docs/plan.md#data-handling). In short:

- Production agents use **Claude on Amazon Bedrock in the EU**, with **data retention `none`**
  enforced by an SCP, so it can't be loosened by accident.
- Agents see **derived metrics through logged tools**, never raw tables or files.
- **Never sent:** GPS, activity names and descriptions, profile data, Garmin IDs, credentials.
- **Off unless explicitly enabled:** weight and body composition, nutrition, hydration, menstrual data.

## Alternatives considered

- **Direct Claude API.** Acceptable fallback: commercial terms, no training. Not first choice,
  because Bedrock keeps Anthropic out of the data path and gives an enforceable retention control.
- **Consumer Claude plans.** Never used for app data.
- **Models requiring retention.** Claude Fable 5.x requires `aws_review`, so it isn't used under
  `none` unless that is consciously decided later. Facts checked on 2026-10-25.

## Consequences

- **Correction pending (AWS session's check, 2026-11-01; not independently verified):** Sonnet 5.5
  and Opus 5.5 have no in-Region option in the EU on `bedrock-runtime` (Opus 5.5 only in London), so
  the `eu.` inference profile can process in six EU Regions. All are in the EU, but "in an EU
  region" means "within the EU" rather than one Region. Also, `none` has a documented exception:
  Bedrock may store and review content flagged as suspected CSAM. See ADR 0008.
- **Open:** which Region's retention setting governs a cross-Region `eu.` request (OPEN_QUESTIONS).
- The SCP needs a member account, because SCPs don't apply to the management account (ADR 0008).
- Phase 3 needs an agent read layer that makes this enforceable, not just a convention
  ([agent layer contract](../docs/contracts/agent-layer.md)).
