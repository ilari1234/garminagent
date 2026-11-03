# Open questions

Everything unresolved or deferred, as of 2026-11-01. Each item has enough context to pick up cold.
**Owner** means only the project owner can answer or do it. Remove an item when it's settled, and
record the answer in an ADR if it's a decision.

This repo is public: report findings as structure and counts, never real values.

## Garmin API access: confirmed or assumed?

**Real access is confirmed, not assumed, for what has actually run:**

| Item | State | Evidence |
|---|---|---|
| Key and token flow, all M0 endpoints | **Confirmed** on real data, 2026-10-24 | [evaluation.md](docs/garmin-personal-api/evaluation.md) |
| `sync` with daily summaries, activities and file download | **Confirmed**: a real run was seen working, 2026-10-28 | Owner's log output in the planning session; not recorded in the repo |
| A complete backfill to the end | **Unknown**: the last output seen was mid-run | |
| FIT parsing on real files | **Not confirmed**: tests are synthetic | |
| Sleep, HRV and weight sync on real data | **Not confirmed**: M4c tests are synthetic | |
| Local scheduler over days | **Not confirmed** | |
| Garmin's rate limits | **Unknown**: no headers and no throttling at 1 req/s; a long run hasn't been judged | |
| Personal API terms for this use | **Assumed from the docs**: single-user, own data. No legal review | |

1. **Real-data check (owner, in own terminal; blocks trusting M6).** Run a full sync, then
   `status`, and report counts: complete or not, pending files, parse failures. Claude must not run
   this and must not read `data/`.
2. **Weight units (owner plus a structure-only check).** The spec says kilograms, but Garmin's web
   APIs have used grams, so `normalize.py` treats values of 1000 or more as grams. No real weigh-in
   has confirmed this. Check with a script reporting which branch applied, not values, in the owner's
   terminal. M6e excludes weight until then.
3. **Sample volume.** Rows and bytes of `activity_sample` on real data decide if Postgres stays
   adequate (ADR 0003) and whether `db.t4g.micro` suffices (ADR 0008). Report counts only.

## Awaiting owner review of plans

4. **Approve or change the phase 2 plan** (ADR 0013), in session
   `local_fd1bf39d-b9af-4b7f-839f-0941182001a1`. Its four questions:
   - HR load coefficient `b` is sex-specific (1.92 / 1.67). Proposal: a setting
     `ANALYTICS_TRIMP_B` instead of storing profile data. Which default?
   - Create the agent database role in M6f, or wait for phase 3?
   - Is HR-only running load acceptable for phase 2?
   - M6a's `renormalize` + `analyze --check` is the first real-data run; can the weight check join it?
5. **Approve or change the AWS plan** (ADR 0008), in session
   `local_3947e9f8-3400-48e0-b30a-f47d3670fd2b`. Defaults unless the owner objects: `eu-north-1`,
   Terraform, re-sync migration, the listed exceptions to "only `infra/`".

## AWS facts not yet verified

Reported by the AWS planning session, 2026-11-01. Recheck; vendor facts change.

6. **Which Region's retention setting governs an `eu.` cross-Region request?** Docs say "the Region
   where you set it" but don't address routing. Plan: set `none` in the source Region and the
   default-enabled destinations and test, which doesn't prove behavior in opt-in Regions (Milan,
   Spain). Stricter option: Opus 4.8 on `bedrock-mantle` in-Region in Stockholm, an older model.
   **Blocks sending any real prompt** under ADR 0006 until the owner accepts an answer.
7. **Are the control-plane and mantle retention settings one value?** Undocumented; the script
   writes and reads both.
8. **Is `db.t4g.micro` orderable for PostgreSQL 18 in the Region?** Check before B3c.
9. **Smaller checks at implementation:** the OIDC subject format on the first run (this repo uses the
   newer format with IDs); the action name for denying long-lived Bedrock API keys; whether
   `id-token` is blocked on fork PRs; TFLint's AWS ruleset can be pinned; prices.

## Phase 3 design (not started)

ADR 0007 sets principles only.

10. **Plan schema:** what a plan and a session are; how events and sports differ (half marathon,
    marathon, ultra, road or gravel cycling, off-season).
11. **Safety limits:** which ones, what numbers, on which metric. Early candidates were weekly load
    increase, acute:chronic ratio and rest days. **Conflict:** the phase 2 proposal omits the
    acute:chronic ratio because of methodological problems. Decide: drop it, or compute it only for
    a limit. Thresholds shouldn't be invented by a model; their source needs documenting.
12. **Lifecycle:** draft, evaluated, approved, active, superseded? Does any adjustment skip approval?
    Default: no.
13. **What counts as evaluation:** the evaluator agent's inputs and how plan-versus-actual is measured.
14. **Agent framework, tool transport (direct or MCP) and model per agent.** The plan mentions "tools
    / an MCP server" without choosing. Fable 5.x is excluded under retention `none` (ADR 0006).
15. **Tool call log:** where it lives, retention. Its fields are specified; its storage isn't.
16. **Prompt-injection tests:** what they feed in, given free text is never sent.
17. **Opt-in config** for weight, nutrition, hydration and menstrual data: its shape and defaults.
18. **Where plans live** if the store later moves to AWS: plans are the first data that can't be
    re-synced, so backups become essential.

## Phase 4 (outline only)

19. **UI type and hosting**, and how it authenticates. Depends on B3.
20. **FIT workout export:** which FIT SDK capability, and does the FIT license allow it as intended?
    Not checked.

## Legal and licensing

22. **Repo license (owner).** None exists, so default copyright applies. Any choice must not be
    copyleft (ADR 0004).
23. **FIT license safety clause (owner).** Judged low risk for advisory, owner-approved plans. Revisit
    when phase 3 makes plans real.

## Owner-side settings (not in git)

24. "Automatically delete head branches" is off; PR merges use `--delete-branch`, so low priority.
25. Non-provider secret patterns are off in GitHub secret scanning.
26. Dependabot's uv updates and the dependency graph can't process `uv.lock` on Python 3.15. Recheck
    periodically and drop the manual monthly step when fixed.
27. Whether to keep GitGuardian long term (advisory now; false positives on env-var syntax in URLs).

## Technical deferred items

28. **`activity_file` retries:** `unavailable` and `invalid` are never retried. No command exists to
    force a retry.
29. **Parquet or downsampling** for samples: only if item 3 says so.
30. **Grade-adjusted pace** for running load: pending a check of Minetti's coefficients against the
    paper (ADR 0013).
31. **HRV smallest worthwhile change:** ±0.5 SD is convention; the AIS guide's formula wasn't confirmed
    because its PDF couldn't be opened (ADR 0013).
32. **The scheduler health check** relies on `status --max-age-hours 13`. Whether it's noisy in
    practice is unknown.
