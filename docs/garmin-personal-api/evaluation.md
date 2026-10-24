# Garmin Connect Personal API — evaluation (M0)

Probe run 2026-10-24 with `spikes/probe_personal_api.py --probe-limits`, target date
2026-10-23. Findings come from `report.json` only (status, timing, headers, payload
structure); no health values were inspected.

## Verdict

Usable as the sole data source for phase 1. Every endpoint answered; auth is simple; one
call returns a full year of daily summaries; history goes back to at least 2019. Gaps
(no training status/readiness, no write access) are as expected from the spec.

## Authentication

- `POST /personalapitoken-service/personal-api/personal-api-key/token` with
  `Authorization: ApiKey <key>` → **HTTP 200, `text/plain`, body is the bare access token**
  (a JWT, ~1.1 KB). Not JSON, no `expires_in` field.
- Data calls: `Authorization: Bearer <token>` works on every endpoint.
- Lifetime is documented as 24 h; since the response carries no expiry, the client should read
  `exp` from the JWT payload (or assume 24 h minus a margin) and also re-request on 401.

## Rate limits

- No rate-limit headers on any response (`X-RateLimit-*`, `Retry-After` absent).
- 31 calls at ~1 req/s: no 429s. Latency 150–700 ms (largest: one-year daily summaries, 600 KB).
- Limits are therefore unknown. Client defaults: ≤1 req/s, exponential backoff on 429/5xx,
  stop the run after repeated 429s rather than retrying hard.

## Endpoint results

| Call | Status | Notes |
|---|---|---|
| userprofile settings, HR zones, fitness age | 200 | |
| activity search (recent 20 / oldest 1) | 200 | 85 fields per item; oldest activity 2019-05-04 |
| activity detail | 200 | `summaryDTO` + `splitSummaries` |
| activity workouts | 200 | empty list for an activity without a structured workout |
| activity file download | 200 | `application/x-zip-compressed`, `attachment; filename="<activityId>.zip"`, starts with zip signature `PK` (spike saves it as `activity_file.bin`) |
| HRV day / range (28 d) | 200 | range: `hrvSummaries[]` |
| sleep day / range (28 d) | 200 | 34 fields per day |
| daily summaries v2 (365 d) | 200 | `{"data": [...]}`, one item per day |
| user summary day | 200 | |
| steps (28 d), stress (28 d) | 200 | |
| body battery (7 d) | 200 | |
| SpO2 day, daily heart rate | 200 | daily HR is intraday (~14 KB/day) |
| weight range (28 d) | 200 | |
| **weight all (28 d)** | **400** | empty `{}` body — cause unknown; use `weight/range?includeAll=true` |
| hydration, wellness activity | 200 | empty lists (no data logged) |
| nutrition | 200 | near-empty (no data logged) |
| menstrual summary / dayview | 204 | no data |

## Range limits (measured)

| Endpoint | Documented | Measured |
|---|---|---|
| sleep summaries | 31 days | 32 days accepted → not strictly enforced; use 31 |
| stress daily | 28 days | 29 days → **400**, enforced |
| daily summaries v2 | 1 year | 366 days accepted; use 365 |

Treat documented limits as the contract; don't rely on leniency.

## History depth

- Daily summaries for a month 5 years back (2021-09/10): 31 items → wellness history
  available at least that far.
- Oldest activity: 2019-05-04.
- Backfill cost estimate for ~7 years: daily summaries ≈ 7 calls; sleep ≈ 85 calls (31 d);
  stress/steps/HRV ≈ 90 calls each (28 d); activities paged by 99 + one detail + one file per
  activity. A full backfill is a few thousand calls → spread it over multiple runs.

## Data relevant to planning

- **Activities (search item):** type/subtype, event type, start local/GMT, distance, durations
  (timer/moving/elapsed), elevation, avg/max HR, cadence, aerobic/anaerobic training effect +
  label, `activityTrainingLoad`, intensity minutes, `hrTimeInZone_1..5`. Fields are omitted when
  not applicable (e.g. power only for activities with power) — models must treat all fields as
  optional.
- **Daily summaries v2:** resting/min/max HR, stress breakdown, Body Battery
  (min/max/charged/drained), intensity minutes, respiration, calories, steps, hydration.
- **Recovery:** HRV summaries (nightly, baseline, status), sleep (34 fields incl. score).
- **Per-second streams:** only via the activity file (zip → presumably FIT; to be confirmed by
  the M2 client, which unzips and checks the FIT signature).

## Consequences for the design

1. Token manager: treat the body as the token; derive expiry from JWT `exp`; refresh on 401.
2. Per-endpoint chunk sizes in config: sleep 31, stress/steps/HRV 28, daily summaries 365,
   body battery 31.
3. Prefer daily summaries v2 for wellness backfill (cheapest); fetch sleep/HRV/stress ranges
   for detail.
4. Activity files: unzip, validate, store the original zip in the raw store and the FIT inside
   for parsing.
5. Treat 204 and empty lists as "no data", not errors; record them so they aren't re-fetched
   every run.
6. All payload fields optional; keep the raw payload so new fields can be mapped later.
7. Conservative pacing and resumable backfill, since limits are unknown.

## Open questions

- Cause of the 400 on `weight/all/{start}/{end}`.
- Contents of the activity zip (expected: one `.fit`).
- Actual rate limit; whether a long backfill triggers throttling.
- JWT `exp` value vs. documented 24 h.
