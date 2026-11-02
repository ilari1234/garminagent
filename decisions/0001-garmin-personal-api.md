# 0001: Official Garmin Personal API as the only data source

**Status:** Accepted (owner's decision, 2026-10-24).

## Context

The app plans training from the owner's Garmin Connect history, so everything depends on how that
data can be obtained. Evaluation evidence: [evaluation.md](../docs/garmin-personal-api/evaluation.md).

## Decision

Use the Garmin Connect Personal API, with the owner's own API key, as the only data source. No
unofficial clients.

## Alternatives considered

- **`python-garminconnect` / `garth` (unofficial clients).** Evaluated first, at Claude's
  suggestion, and rejected by the owner, who named the Personal API instead. The owner's reason
  wasn't stated; the recorded rule in `docs/plan.md` is simply "no unofficial clients".
- **Garmin's Business Developer Program.** Not available: applications were paused and it is for
  businesses.

## Consequences

- **Single user by terms.** The Personal API excludes apps serving multiple end users. The schema
  keeps `athlete_id` so this isn't a structural dead end, but multi-user isn't planned.
- **Read-only, pull-only.** 26 GET endpoints, no webhooks, so we poll on a schedule (ADR 0002).
- **No workout or calendar push.** Plans live in our app; FIT workout export is phase 4.
- **No Training Status, Readiness or acute/chronic load.** We compute load and fitness ourselves
  (phase 2, ADR 0013).
- **Rate limits unknown.** No rate-limit headers and no throttling at 1 req/s in M0. Hence pacing,
  a request budget and a hard stop on rate limiting.
- **Measured quirks:** the token is plain text, not JSON (it crashed the first spike parser);
  `weight/all` returns 400, so use `weight/range?includeAll=true`; only some range limits are
  enforced, so documented limits are treated as the contract.
- **Key handling:** the key expires (30/90/180/365 days) and is shown once; the 24 h token is
  never persisted. See SECURITY.md.
