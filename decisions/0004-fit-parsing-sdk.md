# 0004: `garmin-fit-sdk` for FIT parsing, behind one module

**Status:** Accepted (owner's decision, M4b).

## Context

Per-second samples and laps exist only in the FIT files. FIT content is untrusted input.

## Decision

Use Garmin's official `garmin-fit-sdk`, only through `garminagent.fit` (`parse_fit`). Take only
known numeric fields; ignore developer fields, text and positions; out-of-range values become NULL;
caps of 64 MiB and 400,000 records. Each file records `parse_status` and `parser_version`, so
raising `PARSER_VERSION` re-parses stored files without new requests.

## Alternatives considered

- **`fitdecode` (MIT).** The named fallback if Garmin terminates the license. Not chosen: the owner
  preferred the official SDK.
- **`fitparse`.** Rejected: looked stale.

## Consequences

The SDK is under Garmin's *FIT Protocol License Agreement*, not an open source license:

- Never vendor or redistribute it. The GHCR image contains it, so **the image stays private**
  (verified during M4b: anonymous access returned HTTP 403). The same applies to ECR.
- Don't modify it. Don't put this repo under a copyleft license.
- Garmin may terminate with 30 days' notice. The single module makes the swap cheap.
- Its broad "no safety-critical use" clause was judged low risk because plans are advisory and
  owner-approved with limits in code. This is the owner's call to revisit.
- Robustness lesson: unguarded SDK calls could crash every run on one bad file. Calls are guarded,
  and a bad file is marked `invalid` with a fixed reason code.
- Parsing hasn't been confirmed on the owner's real files (OPEN_QUESTIONS).
