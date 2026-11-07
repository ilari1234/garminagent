# Open questions

Everything unresolved or deferred, as of 2026-11-04. Each item has enough context to pick up cold.
**Owner** means only the project owner can answer or do it. Remove an item when it's settled, and
record the answer in an ADR if it's a decision.

This repo is public: report findings as structure and counts, never real values.

## Garmin API access: confirmed or assumed?

**Real access is confirmed, not assumed, for what has actually run:**

| Item | State | Evidence |
|---|---|---|
| Key and token flow, all M0 endpoints | **Confirmed** on real data, 2026-10-24 | [evaluation.md](docs/garmin-personal-api/evaluation.md) |
| `sync` with daily summaries, activities and file download | **Confirmed**: a real run was seen working, 2026-10-28 | Owner's log output in the planning session; not recorded in the repo |
| A complete backfill to the end | **Confirmed** 2026-11-03: two years, every dataset current | Real-data check |
| FIT parsing on real files | **Confirmed** 2026-11-03: every stored FIT file parsed, none invalid | Real-data check |
| Sleep, HRV and weight sync on real data | **Confirmed** 2026-11-03, except sleep average HR (item 33) | Real-data check |
| Local scheduler over days | **Not confirmed** | |
| Garmin's rate limits | **Unknown**: no headers and no throttling at 1 req/s; a long run hasn't been judged | |
| Personal API terms for this use | **Assumed from the docs**: single-user, own data. No legal review | |

The real-data check ran on 2026-11-03 (`scripts/real_data_check.py`; summary in
[evaluation.md](docs/garmin-personal-api/evaluation.md#real-data-check-2026-11-03)) and settled the
former items 1–3: sync is complete and current for every dataset, every activity file was stored and
its FIT file parsed, weight arrives in **grams** (the normalizer's conversion is right), and
`activity_sample` is small enough that Postgres and `db.t4g.micro` are fine. A second run settled
its follow-ups: Garmin's sleep summaries carry `avgHeartRate` but always null (and no other sleep HR
key), so sleep average HR isn't available from this endpoint; and sample gaps set ADR 0013's gap
rule to 10 s.

## Awaiting owner review of plans

The phase 2 plan was accepted on 2026-11-03 (ADR 0013), with the owner's answers recorded there.

The AWS plan was accepted on 2026-11-03 (ADR 0008, [docs/aws-deployment.md](docs/aws-deployment.md)).
Its retention question (cross-Region scope, control plane vs mantle) was settled by setting `none` in
all six `eu.` destination Regions on both APIs.

## AWS checks before implementation

Reported by the AWS planning session, 2026-11-01. Recheck; vendor facts change.

8. **Is `db.t4g.micro` orderable for PostgreSQL 18 in `eu-north-1`?** Owner runs
   `aws rds describe-orderable-db-instance-options` before B3c. Decided together with item 3; the
   fallback is `db.t4g.small`.
9. **Smaller checks at implementation:** the OIDC subject (this repo uses the newer format with IDs;
   confirm from GitHub's API before B3c, not from token claims in public logs); the action and
   condition for denying long-lived Bedrock API keys (B2); whether `id-token` is blocked on fork PRs
   (low priority: the environment rule covers it); prices (B3c). *Resolved in B0:* TFLint's AWS
   ruleset is pinned by hash after verifying its release attestation (ADR 0008).

## Phase 3 design (not started)

ADR 0007 sets principles only.

10. **Plan schema:** what a plan and a session are; how events and sports differ (half marathon,
    marathon, ultra, road or gravel cycling, off-season).
11. **Safety limits:** which ones, what numbers, on which metric. Early candidates were weekly load
    increase, acute:chronic ratio and rest days. **Conflict:** the phase 2 proposal omits the
    acute:chronic ratio because of methodological problems. Decide: drop it, or compute it only for
    a limit. Phase 2's suggestion (ADR 0013): keep it out of analytics, and let phase 3 compute it
    only if a limit chosen from documented sources needs it. Thresholds shouldn't be invented by a
    model; their source needs documenting.
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
30. **Grade-adjusted pace** for running load: pending a check of Minetti's coefficients against the
    paper (ADR 0013).
31. **HRV smallest worthwhile change:** ±0.5 SD is convention; the AIS guide's formula wasn't confirmed
    because its PDF couldn't be opened (ADR 0013).
32. **The scheduler health check** relies on `status --max-age-hours 13`. Whether it's noisy in
    practice is unknown.
34. **Phase 1 review findings** ([docs/reviews/phase1-review.md](docs/reviews/phase1-review.md),
    2026-11-04). H1 and M1–M4 are fixed in #19 ([ADR 0015](decisions/0015-sync-failure-handling.md))
    and re-checked; phase 1 is done. The re-check's N1–N3 are fixed in #21. Still open, all low:
    L1–L11 from the review. Owner: track A.
35. **Does the Personal API send sentinel values** (−1 or −2 for stress, 0 for HR) in daily summaries
    or activities? Since #19 they become NULL at parsing either way; rows stored earlier keep them
    until re-normalized: `garminagent renormalize <dataset>` (M6a) rewrites them. Counting out-of-range
    values in the real-data check would show whether that matters.
36. **Activities deleted or merged at Garmin** are never removed locally; sync only upserts. Decide
    before M6b and M6c whether to reconcile, for example by flagging activities missing from a
    refetched range, since phantom activities would inflate load.
37. **Migration owner role locally.** Since #19 the owner password no longer reaches the app
    containers (compose blanks it, ADR 0015), but the owner role is still the Postgres superuser.
    Decide whether to also create a non-superuser schema owner in `docker/postgres/init`.
38. **M6a's unverified Garmin semantics**, to check with the owner's first `renormalize activities`
    and `analyze` on real data, before M6b relies on them (counts only):
    - `getlactateThresholdSpeed` is converted from the spec's cm/ms to m/s. If it actually arrives in
      m/s, every value lands outside 0.5–10 m/s and becomes NULL: `threshold_speed` rows would be 0.
    - `intensityFactor` is read as the spec's fraction (0.80); a percentage would become NULL.
    - `lactateThresholdBpm` and `maxFtp` on an activity may be the profile value at the time rather
      than something measured in it. Either way they are change points in the timeline; LTHR counts
      for the activity's own sport group, FTP only from rides, threshold speed only from runs.
    - HR zones are requested with `generateDefaults=false`, so only configured zones come back. If
      the owner's account answers with an empty list, Garmin zones contribute nothing and thresholds
      fall back to activity values (`hr_zones.records` = 0 in the sync counts).
