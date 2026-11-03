# Agent layer contract

**State: proposed. Nothing here is built.** Rules marked **Required** come from accepted decisions
(ADR 0006, ADR 0007) and bind whatever design phase 3 picks. Items marked **Proposed** come from the
unapproved phase 2 plan (ADR 0013). Items marked **Open** are in OPEN_QUESTIONS.

## Read boundary: analytics to agents

**Required**

- Agents see derived metrics through tools only, never tables, files or raw data.
- Tool output never contains GPS or tracks, activity names or descriptions, profile data, Garmin IDs
  or credentials.
- Weight and body composition, nutrition, hydration and menstrual data are off unless enabled in
  config.
- Every tool call is logged: tool, arguments, returned payload size and fields, timestamp.
- Tool output is data, never instructions.

**Proposed (M6f)**

- An `agent` database schema of views exposing only allowed columns, and a `garminagent_agent` role
  that can read only that schema, so a tool bug can't reach `raw_payload` or `activity_sample`.
- `analytics.queries`: typed records, date ranges capped (for example at 365 days), limited row counts.
  Each function also returns the list of fields it sent, ready for the tool call log.
- Sessions are referred to as `2026-10-12/1` (date and sequence), never by Garmin ID.
- An allowlist test: no `*_id`, `lat`/`lon`, `name`, `description` or `note` field anywhere in the
  agent views or records.
- `db-bootstrap` (B3b) must also create this role on AWS.

**Proposed tools**

| Tool | Returns |
|---|---|
| `training_load` | Daily load, fitness, fatigue, form |
| `weekly_summary` | Weekly rollup |
| `sessions` | Per-session summary: date, sport, duration, distance, load, TE, average HR, pace, power |
| `zone_distribution` | Time in zones |
| `best_efforts` | Curves and bests by range and sport |
| `recovery_trend` | HRV, resting HR, sleep, Body Battery and stress trends, with flags |
| `current_thresholds` | Thresholds in effect |

Weight isn't exposed.

## Model boundary: tools to Bedrock

**Required:** Bedrock in the EU with retention `none` verified before any prompt, global inference
denied, `none` set in all six `eu.` destination Regions (ADR 0008). Credentials never in prompts.
**Open:** framework,
transport and model per agent.

## Write boundary: agents to plans

**Required**

- **One write path.** A single function stores a plan version. No other agent-reachable code writes
  to the database.
- **Validated before storage:** schema, then plausibility, then safety limits. All are code. A failed
  plan isn't stored as usable; the agent gets a structured reason.
- **Append-only versions**, each with the inputs it was based on and the reasoning shown to the owner.
- **Approval:** only the owner's explicit action makes a version active. No agent tool can do this.
- **Bounded:** iteration and token caps per run.
- **No shell, code or general tools.**

**Open:** plan schema, lifecycle states, limit values, how adjustments relate to versions, where the
tool log lives (OPEN_QUESTIONS 10–15).

## Tests a phase 3 implementation must have

- Agent role can't SELECT raw or normalized tables.
- Field allowlist over all tool outputs.
- A plan breaking each safety limit is rejected, whatever the model says.
- No agent tool can activate a plan.
- A prompt-injection case (design open).
- Tool call log written for every call, with no health values in application logs.
