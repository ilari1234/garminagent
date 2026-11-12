# B1 foundation review

Independent review under ADR 0012, done 2026-11-12 by the `reviewer` subagent. It compares the
`infra-b1` work (base `origin/main` `f764ca0`, uncommitted at the time) with what the B1 section of
docs/aws-deployment.md and its row in *Security review per stage*, ADR 0008, ADR 0017,
SECURITY.md, docs/tracks/infrastructure.md and CLAUDE.md say B1 delivers.

## Verdict

**Ready after fixes.** No high findings. The Terraform delivers what the spec lists: the
organization, the OU and the member account; centralized root access, switched on before the
account exists; both permission sets; the budget and its action; the organization trail; both
state buckets; the KMS key; OIDC; and Access Analyzer. The IAM and trust policies are tight.

Two medium findings were on the live path once the owner applies: M1, a checklist command that
could stop the organization trail, and M2, an unverified and untested trust condition on the
budget action. M3 is a process gap. All findings are resolved in the same PR
([Resolution](#resolution)).

| Part | Verdict | Findings |
|---|---|---|
| `infra/org`: organization, OU, account, root access | Done | none |
| `infra/org`: permission sets | Done | L3 |
| `infra/org`: budget, SCP, action role | Done, one unverified risk | M2, L2 |
| `infra/org`: buckets and trail | Done | L2 |
| `infra/account`: KMS, state bucket, OIDC, Access Analyzer | Done | L1, L2 |
| Tests vs SECURITY.md claims | Mostly hold | L2 |
| Owner checklist | Order correct; one dangerous command | M1, L5 |
| Docs: state, ADR, SECURITY.md, build notes | Mostly consistent | M3, L4 |

## Method

**Read.** Every changed `.tf`, `.tftest.hcl`, mock and example file in the three roots. The full
checklist and the B1 build notes. `infra.yml` and the deny rules, for the CI and deny-rule
claims.

**Ran.** Everything ran on a scratch copy of `infra/`, with the digest-pinned images from
`infra.yml`:
- `terraform fmt`, `init -lockfile=readonly`, `validate` and `test`: all pass.
- Trivy 0.76.1, after a cosign check against the pinned signer: 0 misconfigurations. Exactly the
  five documented exceptions are ignored (AVD-AWS-0089 twice, 0132, 0015, 0162), and none of them
  is ignored without firing.

**Probes.** Extra test runs against the suite's own mocks, synthetic values only:
1. The budget role's `aws:SourceArn` is the bare budget ARN.
2. The org example placeholders fail validation.
3. Both member-bucket encryption denies use `IfExists`.
4. The account example placeholders fail validation.

**Not touched.** No AWS commands, `plan`, `apply` or state. No `.env*` or `data/`. Only
synthetic IDs and emails were found.

## Findings

| ID | Sev | Finding |
|---|---|---|
| M1 | Medium | Checklist section 8's "members can't touch the trail" check ran `stop-logging` with no `--profile`. In a shell left on the management profile, it stops the organization trail. |
| M2 | Medium | The budget role's trust pinned `aws:SourceArn` to the bare budget ARN; AWS's example uses `budget/*`. If Budgets presents the action's ARN, the backstop fails silently when it fires, and nothing exercised that path. *Unconfirmed* which ARN is presented. |
| M3 | Medium | PROJECT_STATE.md still showed B1 as "Next"; ADR 0008's status line omitted B1. |
| L1 | Low | The member state bucket let through a PutObject asking for `aws:kms` without a key ID. S3 encrypts such an object with `aws/s3`, so the claim "other encryption refused" didn't fully hold. *Unconfirmed* against AWS; Terraform itself always sends the key ID. |
| L2 | Low | Tests were thinner than the docs claimed. The rejection tests didn't use the example files' literal placeholders, and `sso_user_name` wasn't tested. The budget role's `ArnLike` wasn't asserted. Neither were the OIDC `Principal.Federated` and `Action`, or the trail name behind `local.trail_arn`. |
| L3 | Low | The ManagementAdmin Bedrock deny is a guardrail, not a boundary: an admin can create a role without it. The docs said it is what keeps model calls out. |
| L4 | Low | Doc mismatches. "Applied by the owner" was written before any apply. A wrong checklist section was cited for the member root check. The incident step said Terraform restores the policy, which isn't true for WorkloadAdmin. There was a double blank line. |
| L5 | Low | Checklist robustness. Section 6 hard-coded the account name with no empty check. Section 9's `lookup-events` had no `--region`. The alert test was optional, although the stage row lists "budget alerts fire". |

## Contract invariants

| Invariant | Holds? |
|---|---|
| Every provider sets an explicit EU region (GA001 clean) | Yes |
| Exact provider pin, Terraform `~> 1.17.0`, lock files, no modules | Yes |
| Nothing account-shaped in the repo or in `for_each` keys; emails and the SSO user `sensitive` | Yes |
| CI without AWS credentials or `id-token`, never `plan` | Yes |
| `allowed_account_ids` in every root | Yes |
| OIDC trust: exact `sub` and `aud`, `StringEquals` only | Yes |
| No permissions on `gha-ecr-push` in B1 | Yes, by inspection |
| Budget role: confused-deputy conditions, one SCP on one OU | Yes (scope); M2 for the form |
| Centralized root access before the member account exists | Yes (`depends_on` chain) |
| Trail: organization, multi-Region, global events, validation; bucket `aws:SourceArn` pinned | Yes |
| Buckets private, ACLs off, versioned, TLS only, `prevent_destroy` | Yes |
| Member state SSE-KMS with the account key | Yes; L1 for the edge |
| KMS: rotation, 30-day window, IAM-delegation policy only | Yes |
| Build notes outside ADRs (ADR 0017) | Yes |
| PROJECT_STATE updated in the milestone PR | No (M3), fixed |

## Not checked

- **Live AWS behaviour.** Only the owner's apply can show these:
  - the import plan;
  - that a second plan shows no changes;
  - that Budgets accepts an absolute action threshold above the budget;
  - which ARN Budgets presents to the role (M2);
  - S3's key choice in L1;
  - the CloudTrail service-linked role.
- **GitHub's ID-based OIDC subject for environments.** The format is inferred, so a mistake fails
  closed. Checklist section 9 covers it.
- **TFLint.** Not run (CI only).

## Resolution

Fixed in the same PR, 2026-11-12. Terraform tests afterwards: org 11, account 9, workload 2, all
passing.

- **M1:** both commands carry `--profile garminagent-workload`. `get-trail-status` with the
  management profile then confirms `IsLogging` is still true. If it isn't, the checklist gives
  the `start-logging` command.
- **M2:** `aws:SourceArn` matches the budget and `budget/<name>/action/*`, with
  `aws:SourceAccount` still pinned. A test asserts both conditions. Checklist section 8 now
  requires firing the action once with a tiny budget, confirming the SCP attaches, then reversing
  it.
- **M3:** the PROJECT_STATE B1 row, the infra line and ADR 0008's status line are updated (by
  `milestone-close`).
- **L1:** new `DenyKmsWithoutKeyId` statement, and the test's Sid set is extended.
- **L2:** a `rejects_the_example_file_placeholders` run in each root uses the literal example
  values. The OIDC principal and action, the budget `ArnLike` and the trail name are asserted.
- **L3:** the code comment, the SECURITY.md threat row and the build notes call the deny a
  guardrail. The optional extra denies (`bedrock-agentcore:*`, `iam:CreateUser`,
  `iam:CreateAccessKey`) aren't added and are left for B2's review.
- **L4:** the wording, the section reference and the incident step are corrected, and the blank
  line is removed.
- **L5:** the account name is a variable with a 12-digit check, `--region eu-north-1` is added,
  and the alert test is required.
