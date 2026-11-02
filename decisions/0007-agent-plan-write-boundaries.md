# 0007: How agents may create or change fitness plans

**Status:** Principles accepted (recorded in SECURITY.md and `docs/plan.md` during phase 1 planning).
The detailed design is **open** and belongs to phase 3; nothing is built.

## Context

Agents will create, update and evaluate training plans. The project's top risk isn't a data leak but
a plausible, wrong plan that injures the owner (OWASP LLM09 Misinformation). A secondary risk is an
agent doing more than intended, or being steered by content it reads.

## Decision

Principles any phase 3 design must keep:

1. **Agents have no direct database access.** Reads go through narrow, read-only, logged tools.
2. **Exactly one write path for plans.** It validates before storing. No other tool writes.
3. **No shell, code execution or general-purpose tools** for agents.
4. **Validation in code:** a plan must pass schema and plausibility checks before it is stored or
   exported.
5. **Safety limits are code, not prompts.** A model can't override them or talk its way past them.
6. **Change history:** plans are versioned documents stored with the inputs they were based on. A
   change adds a version; it never overwrites one. Inputs are validated again before reuse.
7. **A human approves.** A plan becomes active only after the owner approves it. The plan shows its
   inputs and reasoning.
8. **An evaluator agent** reviews plans, in addition to the code limits, not instead of them.
9. **Bounded runs:** iteration and token caps.
10. **Garmin-sourced text is data, never instructions**; free text isn't sent at all (ADR 0006).

## Alternatives considered

- **Prompt-only guardrails.** Rejected: prompts can't guarantee a limit.
- **Agents writing directly to tables.** Rejected: too broad for a single validation point.
- **Automatic activation of adjusted plans.** Not chosen: whether small adjustments could ever skip
  approval is an open question; the default is that they can't.

## Consequences

Not decided, for phase 3 to settle (details in OPEN_QUESTIONS): plan schema; the numeric limits and
what they're measured on; plan lifecycle states; how an adjustment differs from a new version;
agent framework and tool transport (direct tools or MCP); model per agent; prompt-injection tests.

The candidate limits in early planning were weekly load increase, acute:chronic ratio and rest days.
The phase 2 proposal deliberately leaves out the acute:chronic ratio because of documented
methodological problems (ADR 0013), so the two must be reconciled before phase 3 fixes its limits.
