# AWS deployment plan

How garminagent moves to AWS with Terraform, in stages the owner reviews one PR at a time. The
decision and the alternatives are in [ADR 0008](../decisions/0008-cloud-deployment.md); this file is
the detail. Accepted by the owner on 2026-11-03. Vendor facts and prices were checked against AWS,
HashiCorp, OpenTofu and GitHub documentation on 2026-11-01 ([sources](#sources)); recheck them at each
stage, because they change.

Nothing here is built yet. Every `terraform apply`, every run against AWS and every secret value is
the owner's, in the owner's terminal.

## Stages

| PR | Delivers | Owner applies |
|---|---|---|
| **B0 tooling** | `infra/` skeleton, provider lock files, CI checks, `.gitignore` and Claude deny rules | none |
| **B1 foundation** | Organization with a `workload` OU and one member account, SSO with MFA, budgets, organization trail, state buckets, GitHub OIDC with a role that has no permissions yet | `org`, `account` |
| **B2 Bedrock** | Retention SCP, Region SCP, `BedrockUser` SSO role, script that sets and verifies retention | `org` |
| **B3a seam** | `make_blob_store(settings)` factory only | none |
| **B3b app code** | S3 `BlobStore`, `db-bootstrap`, RDS CA bundle, TLS guard | none |
| **B3c data and network** | VPC, RDS, S3, KMS, secrets, ECR, image push | `workload` |
| **B3d compute and cutover** | ECS tasks, schedules, alerts, operations guide | `workload` |

**Order.** B1 before B2. B2 works without B3: phase 3 can call Bedrock from the owner's machine while
the data stays local. B3b–d need B1. B3a is code only and may land before B1 (owner, 2026-11-03); it
lands before M6a changes the code next to it; B3b rebases after
any open M6 PR. B3c waits for the real-data check (sample volume decides the database class) and the
orderability check below.

**Every stage** updates ADR 0008 and `PROJECT_STATE.md` when it changes the design (ADR 0012), and ends
with the SECURITY.md checklist and `/security-review`.

## B1 foundation

**By hand, once:**

- Create the management account and set root MFA.
- Enable the IAM Identity Center organization instance in `eu-north-1`. Expected to be a console
  step; confirm during B1. One user, MFA required, no external identity provider.
- After that, Terraform runs with SSO credentials. There are no IAM users and no access keys anywhere.

**In Terraform (`infra/org`, `infra/account`):**

- Organization with all features and SCPs enabled, a `workload` OU and one member account. The member
  account's email address is in a gitignored tfvars file, not in the repo.
- [Centralized root access](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_root-user.html): the
  member account has no root credentials.
- SSO permission sets: an admin set for the management account, which carries an inline deny on
  Bedrock (SCPs don't bind the management account), and an admin set for the member account.
- Organization trail, multi-Region, logs to a bucket in the management account. Member accounts can't
  change or stop it ([CloudTrail](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/creating-trail-organization.html)).
  The first copy of management events is free.
- Budgets in the management account: $25/month on total cost, alerting at 50%, 80% and 100% of actual
  and forecast spend. Claude charges appear under the model provider through AWS Marketplace, not
  under Bedrock, so the budget covers total cost rather than a Bedrock filter.
- A budget action at $50 attaches a deny-invoke SCP to the `workload` OU. Billing data lags, so the
  token and iteration caps in code (ADR 0007) stay the main control.
- State buckets: one in the management account for `infra/org`, one in the member account for
  `infra/account` and `infra/workload`.
- A customer-managed KMS key in the member account, used for state and later for all data.
- GitHub OIDC provider and a role `gha-ecr-push` with no permissions until B3c.
- IAM Access Analyzer for external access.

**Not included**, knowingly: Control Tower, AWS Config, Security Hub and GuardDuty. This gives up
continuous compliance checks and threat detection; the organization trail and Access Analyzer cover
the basics for one user at about $1–2/month.

**Free plan accounts.** Joining Organizations upgrades a Free plan account to a paid one
([terms](https://aws.amazon.com/free/terms/)). AWS's pages disagree on whether credits survive, so the
plan doesn't count on them.

## B2 Bedrock

### Facts that shape it

- Claude Sonnet 5.5 and Opus 5.5 have **no in-Region option in any EU Region** on `bedrock-runtime`;
  Opus 5.5 runs in-Region only in London. From Stockholm, the `eu.` inference profile can process a
  request in **Frankfurt, Stockholm, Milan, Spain, Ireland or Paris**
  ([Sonnet 5.5](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5-5.html),
  [Opus 5.5](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)).
  All six are in the EU; "in an EU region" therefore means "within the EU".
- Retention is set **per Region**, and new accounts start at `inherit`, which falls back to each
  model's default ([data retention](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html),
  [AWS blog](https://aws.amazon.com/blogs/security/enforce-zero-data-retention-on-amazon-bedrock-with-bedrock-projects-and-service-control-policies/)).
  An SCP stops the setting from being changed; it doesn't set it.
- The documentation doesn't say whether a cross-Region request follows the source Region's setting or
  the destination's.
- `none` has a documented exception: Bedrock may store and review content flagged as suspected CSAM
  ([abuse detection](https://docs.aws.amazon.com/bedrock/latest/userguide/abuse-detection.html)).
- The control plane (`bedrock:PutAccountDataRetention`) and the mantle endpoint
  (`bedrock-mantle:PutAccountDataRetention`) each have an API for the setting. Whether they hold one
  value isn't documented.
- The AWS provider has no resource for the setting (checked up to v6.71.0), so a script sets it.
- Anthropic models need the first-use form once per account or organization, and the first call needs
  `aws-marketplace:Subscribe` ([model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)).

### Decisions (owner, 2026-11-03)

- **Retention `none` in all six destination Regions**, so the routing question can't loosen anything.
  Milan and Spain are opt-in Regions: they are enabled in the member account **only** so their setting
  can be set to `none` and read back. The Region SCP denies everything else there.
- **Both retention APIs are treated as separate settings.** The SCP denies changing either one away
  from `none`, and the script writes and reads both.
- **No mantle projects.** The app uses `bedrock-runtime`; the SCP denies `bedrock-mantle:CreateProject`
  and `UpdateProject` unless the mode is `none`.
- **No real prompt** before the verification below has passed (ADR 0006).
- Fallback if any of this fails: Opus 4.8 on `bedrock-mantle`, which runs in-Region in Stockholm
  ([Opus 4.8](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-4-8.html)).
  One Region, older model.

### SCPs, attached to the `workload` OU

AWS recommends testing SCPs on an OU rather than the organization root
([SCPs](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html)).

**Retention SCP:**

- Deny `bedrock:PutAccountDataRetention`, `bedrock-mantle:PutAccountDataRetention`,
  `bedrock-mantle:CreateProject` and `bedrock-mantle:UpdateProject` when `DataRetentionMode` isn't
  `none` (AWS's example, [data retention](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html#data-retention-iam)).
- Deny global cross-Region inference (AWS's example,
  [global inference](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html#global-cris-disable)).
- Deny turning on [model invocation logging](https://docs.aws.amazon.com/bedrock/latest/userguide/model-invocation-logging.html),
  which would store prompts.
- Deny creating long-lived Bedrock API keys. Expected:
  `iam:CreateServiceSpecificCredential` limited to the Bedrock service; confirm the action and
  condition names in the service authorization reference.

**Region SCP:**

- Everything is limited to `eu-north-1`, apart from AWS's standard exemptions for global services.
- Bedrock invocation is also allowed in the other five `eu.` destinations, because one blocked
  destination fails the whole request
  ([inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html)).
- In those five, the retention read and write calls are allowed too. Nothing else.
- London and Zurich aren't destinations from Stockholm and stay denied.

### Order, before any prompt

1. Attach both SCPs to the `workload` OU.
2. Enable Milan and Spain in the member account.
3. Run `infra/scripts/bedrock-retention`: set `none` on both APIs in all six Regions, then read them
   back. It fails loudly on any other value.
4. Run the SCP check (below).
5. Submit the Anthropic first-use form. A GitHub URL is acceptable for the website field.
6. Send one synthetic test prompt through `eu.anthropic.claude-sonnet-5-5`. Success under `none` shows
   the model accepts that mode; a model that needs retention fails instead.

An EventBridge rule on the `PutAccountDataRetention` CloudTrail events emails the owner on any change.
A monthly read of all six Regions joins the routine maintenance in SECURITY.md.

### Checking that the SCP applies

SCPs don't affect the management account, including its root user and admins.

1. From the management account: the organization has all features, SCPs are enabled on the root, and
   both policies are attached to the OU (`list-roots`, `list-policies-for-target`).
2. From the member account's admin role, try to set the mode to `default` on both APIs in each of the
   six Regions. Expect an explicit SCP deny. The test uses `default` rather than `aws_review`, so a
   failed test can't enable human review.
3. Set `none` and read it back.
4. Confirm a global profile (`global.…`) is denied.

**What remains:** a management-account admin can still detach the SCP. The organization trail records
that, and the retention-change alert fires if the setting then changes.

## B3 workload

### Network

- VPC in `eu-north-1` with two public subnets for tasks and two private subnets for RDS (a DB subnet
  group needs two Availability Zones).
- Tasks get a public IP. Their security group has **no inbound rules**; outbound allows 443 anywhere
  and 5432 to the RDS security group only.
- RDS: private subnets with no internet route, `publicly_accessible = false`, inbound 5432 only from
  the task security group.
- Free S3 gateway endpoint.
- **No NAT gateway** ($33.58/month plus $0.046/GB) and no interface endpoints ($7.67 per endpoint per
  AZ). Garmin needs the internet either way.
- **Known limit:** a compromised dependency could send data to any host on 443. A NAT gateway wouldn't
  change that; only a filtering proxy or Network Firewall would, and neither is worth the cost here.

### Data

- **RDS PostgreSQL 18** ([release calendar](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-release-calendar.html)),
  single-AZ, 20 GB gp3, KMS encryption, 7-day automated backups, deletion protection, final snapshot.
- Instance class `db.t4g.micro`, **subject to two checks before B3c**:
  - the owner runs `aws rds describe-orderable-db-instance-options` for `postgres` 18 and
    `db.t4g.micro` in `eu-north-1`;
  - the real-data check reports `activity_sample` rows and bytes (OPEN_QUESTIONS 3).

  If either fails, `db.t4g.small` (+$12.41/month).
- `rds.force_ssl` is on by default from PostgreSQL 15. Clients use `sslmode=verify-full` with the RDS
  CA bundle ([RDS TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html)).
- **S3 blob bucket**: SSE-KMS with a bucket key, public access blocked, TLS-only bucket policy, **no
  versioning**. Versioning would keep what `purge-data` is meant to delete.
- **Secrets Manager:** the RDS master password is managed by RDS
  ([RDS and Secrets Manager](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-secrets-manager.html)),
  so state holds only its ARN. Terraform creates three empty secrets: the Garmin key, `DATABASE_URL`
  and `MIGRATION_DATABASE_URL`. A fourth, `AGENT_DATABASE_URL` for the agent role, is added once M6f
  has defined that role. The owner fills them from a terminal.
- **ECR:** private, immutable tags, keep the last 5 images. The image contains Garmin's FIT SDK and
  must not be shared: no repository policy granting other accounts, and the SCP denies
  `ecr-public:*`.

### Compute and schedules

- **ECS Fargate**, x86 (CI builds amd64 images), 0.5 vCPU and 1 GB until measured after M6.
- Task definitions for `sync`, `status`, `db-upgrade` and `db-bootstrap`. Each has its own execution
  role reading only its own secrets, and its own task role:
  - `sync` and `status`: the Garmin key and `DATABASE_URL`; S3 read, write and delete on the blob bucket.
  - `db-upgrade`: `MIGRATION_DATABASE_URL` only.
  - `db-bootstrap`: the RDS master secret and the database URLs (two, three once M6f exists).
  - Phase 3 agent tasks, later: `AGENT_DATABASE_URL` only.
- Read-only root filesystem, non-root user (from the image), ECS Exec off.
- Secrets are injected as environment variables at task start; a new value takes effect at the next
  task ([ECS secrets](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html)).
  Fine for one-shot tasks.
- **EventBridge Scheduler** ([templated targets](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-targets-templated.html)):
  `sync` every 6 hours, and a 15-minute catch-up schedule enabled only during backfill. No retries
  (runs are idempotent and serialized by the advisory lock); a dead-letter queue catches failed starts.
  The scheduler role may run only these task definitions and pass only their roles.
- **Alerts** by email through SNS:
  - an EventBridge rule on [ECS task state changes](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs_task_events.html)
    when a task stops with exit code 1 or 75, or fails to start (exit codes per the
    [ingestion contract](contracts/ingestion.md));
  - a log-metric alarm when no sync has finished in 13 hours, matching the local health check.
- Logs go to CloudWatch with 90-day retention. They carry counts, dates and stop reasons only.
- **Deploying:** Terraform pins the image digest. The owner runs `db-upgrade` with `aws ecs run-task`
  after applying a new digest.

### Image delivery

A CI job after `image`, on pushes to `main` only, assumes `gha-ecr-push` through OIDC and copies the
same digest from GHCR to ECR. There is no rebuild. The role can push only to the one repository.

The trust policy must use the repository's subject format. Repositories created after 15 July 2026
use owner and repository IDs, for example `repo:OWNER@OWNER_ID/REPO@REPO_ID:environment:ecr-push`
([GitHub](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)).
Confirm it from GitHub's API before B3c, not by printing token claims in public logs. IAM rejects a
GitHub trust policy without a specific `sub`
([IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html)). The role is
also bound to a GitHub environment that only `main` can deploy to.

## App code B3 needs

| Change | PR | Notes |
|---|---|---|
| `make_blob_store(settings)` | B3a | Replaces the direct `LocalBlobStore(settings.blob_dir)` calls in `cli.py` and `runner.py`. |
| `storage/s3.py` | B3b | Implements the `BlobStore` protocol with `boto3` (Apache-2.0). Keys go through `validate_key`; a missing key raises `FileNotFoundError` ([ingestion contract](contracts/ingestion.md)). `boto3` is imported only when S3 is selected. Settings: `BLOB_BACKEND`, `BLOB_S3_BUCKET`. |
| Tests | B3b | One contract suite over the local store and an in-memory fake S3 client. `types-boto3` (MIT) as a dev dependency. No network. |
| `garminagent db-bootstrap` | B3b | RDS has no equivalent of the compose init script, and migrations must not run as the master user. Logs in with the RDS master secret, then creates the owner role, the app role and, once M6f has defined it, `garminagent_agent` (the owner chose M6f for that role, 2026-11-03). The agent role's name and grants come from the single `garminagent.storage` module M6f adds; migration 0011 applies the grants. Each role's password comes from its URL secret (`MIGRATION_DATABASE_URL`, `DATABASE_URL`, `AGENT_DATABASE_URL`); no other secret format. |
| RDS CA bundle | B3b | Committed as a public file and copied into the image, so `verify-full` works. |
| `DB_REQUIRE_VERIFY_FULL` | B3b | Turns the existing TLS warning in `storage/db.py` into a hard failure. Set in the task definitions, because the URLs are entered by hand. |

None of this adds a migration. B3b touches `cli.py`, `config.py`, `pyproject.toml` and `uv.lock`, which
M6 PRs also touch; it rebases after them.

## Terraform layout and state

```
infra/org/       management account: organization, OU, SCPs, SSO, budgets, trail, own state bucket
infra/account/   member account: state bucket, KMS key, OIDC provider and role, Access Analyzer
infra/workload/  member account: VPC, RDS, S3, ECR, secrets, ECS, schedules, alarms
infra/scripts/   bedrock-retention (B2), plan_summary.py
infra/policy/    custom Trivy checks
```

- **State:** S3 backend with [`use_lockfile = true`](https://developer.hashicorp.com/terraform/language/backend/s3)
  (DynamoDB locking is deprecated), versioning, TLS-only policy, public access blocked. The member
  account's state uses SSE-KMS. `infra/org` starts with local state and migrates to its bucket. No
  workspaces.
- **Kept out of git:** `*.tfstate*`, `*.tfplan`, `.terraform/`, real `*.tfvars` (account IDs, emails)
  and `backend.hcl`. Example files hold placeholders only.
- **No secrets in state or code:** RDS generates the master password; secrets are created empty;
  nothing credential-shaped is committed. Terraform's
  [ephemeral resources and write-only arguments](https://developer.hashicorp.com/terraform/language/manage-sensitive-data/ephemeral)
  are available if a secret ever has to pass through Terraform.
- **No third-party modules**: the [lock file pins providers only](https://developer.hashicorp.com/terraform/language/files/dependency-lock).

### Where things run

The repo is public, so CI logs are public (ADR 0010).

| Public CI, no AWS credentials | Owner's terminal only |
|---|---|
| `terraform fmt -check`, `validate` after `init -backend=false -lockfile=readonly` | `plan`, `apply`, any state access |
| `terraform test` with mocked providers, asserting the SCPs' actions and conditions | `bedrock-retention` and the SCP check |
| TFLint, Trivy config scan, zizmor | IAM Access Analyzer policy validation |
| ECR push (main only, through OIDC) | `aws ecs run-task`, putting secret values |

- **No `plan` in CI**, not even read-only: plan output contains ARNs, account IDs and endpoints. This
  gives up automatic plan previews on PRs.
- **Review flow:** the owner runs `plan -out`, then `infra/scripts/plan_summary.py` prints only resource
  addresses and actions, which are safe to share. The owner applies that saved plan. Steps:
  [infra/README.md](../infra/README.md).
- The infra jobs run on every PR, not behind a path filter, because a skipped required check would
  block merging.
- Claude Code is denied reading state, plan and tfvars files, and running `terraform apply` (B0, in
  `.claude/settings.json`).

## Tooling and supply chain

| | Terraform | OpenTofu |
|---|---|---|
| License | [BUSL 1.1 from v1.6](https://github.com/hashicorp/terraform/blob/main/LICENSE): production use allowed unless offered to others in competition with IBM's paid versions. Not OSI-approved. | MPL-2.0 |
| For this repo | Allowed: we neither compete nor distribute it | Allowed |
| State and plan encryption | Backend encryption only | [Built in, enforceable, KMS](https://opentofu.org/docs/language/state/encryption/) |
| Dependabot | `terraform` | `opentofu` |

**Terraform is used** (ADR 0008). Switching to OpenTofu stays about one PR if Terraform-only features
are avoided.

- **Terraform** from a digest-pinned image. Take a release at least 7 days old: 1.17.0 qualified on
  2026-11-01, 1.17.1 from 2026-11-04.
- **AWS provider** at an exact version (MPL-2.0; 6.70.0 qualified on 2026-11-01). `.terraform.lock.hcl`
  committed per root with hashes for Linux, Windows and macOS (`terraform providers lock`).
- **Trivy** config scan from the existing digest-pinned, cosign-verified image
  ([Terraform coverage](https://trivy.dev/docs/latest/guide/coverage/iac/terraform/)). Built-in checks
  cover public RDS and open inbound rules; custom check GA001 (B0) requires an explicit EU region on
  every `aws` provider.
- **TFLint** (MPL-2.0) from a digest-pinned image, with the AWS ruleset installed by a SHA-256 taken
  after verifying its release attestation (B0; ADR 0008).
- **Actions** pinned by SHA. The only new one is `aws-actions/configure-aws-credentials`.
- **Dependabot** `terraform` ecosystem with the 7-day cooldown, for the three roots.

## Cost

Stockholm, before VAT, 730 hours a month, from AWS's public price files on 2026-11-01. Sizes are
assumptions until measured.

| Item | Assumption | $/month |
|---|---|---|
| RDS `db.t4g.micro` | $0.016/hour | 11.68 |
| RDS gp3 storage | 20 GB at $0.12 | 2.40 |
| RDS backups | up to the database size is free | 0.00 |
| Secrets Manager | 4 secrets at $0.40 (3 created plus the RDS-managed one); 5 with `AGENT_DATABASE_URL` after M6f, +$0.40 | 1.60 |
| KMS | 1 customer-managed key | 1.00 |
| Fargate | 20 task-hours at 0.5 vCPU and 1 GB, x86 | 0.54 |
| Public IPv4 | 20 hours at $0.005 | 0.10 |
| S3, ECR, logs, alarm, trail storage | about 5 GB, 0.5 GB, 0.1 GB | about 0.45 |
| **Workload total** | | **about $18** |

- Frankfurt would be about $20.
- B1 and B2 alone: about $1–2.
- First backfill month: under $1 extra.
- **Bedrock tokens** (`eu.` rate per million tokens, input/output): Sonnet 5.5 $2.20/$11, Opus 5.5
  $4.40/$22, Haiku 5.5 $0.11/$0.55. The non-"Global" entries in the price file are read as the `eu.`
  rate, consistent with [global being about 10% cheaper](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html).
  A 50k-input, 5k-output Sonnet run costs about $0.17.

| Cheaper choice taken | Saves | Gives up |
|---|---|---|
| Single-AZ RDS | about $14 | An AZ outage means downtime and a restore; sync retries later |
| No NAT | $33.58 plus data | A static outbound IP; tasks need a public IP |
| No interface endpoints | $7.67 per endpoint per AZ | Private paths to ECR, Secrets Manager and Logs |
| `db.t4g.micro` | $12.41 | Memory and CPU credits; re-measure after M6 |

Not taken: ARM (saves about $0.11 and needs a multi-arch build), the AWS-managed KMS key (saves $1,
gives up key policy control), stopping RDS between runs (limited stop period, complicates every run).

## Moving the data and rolling back

**Re-sync from Garmin**, not a restore. Sync is idempotent and budgeted, no dump of health data leaves
the owner's machine, and it tests the real pipeline. Re-sync loses older raw-payload revisions,
`sync_run` history, any manual thresholds (re-enter them) and anything Garmin no longer keeps. Use the
same `SYNC_START_DATE` as locally.

**Cutover:**

1. Apply B3c and B3d; fill the secrets; run `db-bootstrap`, then `db-upgrade`.
2. Run one `sync` by hand.
3. Pause the local scheduler so both don't call Garmin at once; enable the catch-up schedule.
4. Compare `status` locally and on AWS (date ranges, pending files, no values), plus
   `analyze --check` once M6f exists.
5. Switch to the 6-hour schedule; disable catch-up.
6. Restore one RDS backup to a temporary instance to test it, then delete it.
7. Keep the local stack stopped, not deleted, for 30 days. Update `.env` at each key rotation so
   rollback keeps working.

**Restore instead (untested):** a temporary ECS task with Exec gives an encrypted tunnel for
`pg_restore` from the owner's machine. Use `host=<endpoint> hostaddr=127.0.0.1` to keep `verify-full`,
and `--no-owner --role=<owner role>`. Copy blobs with `aws s3 sync`; keys are unchanged. Remove the task
afterwards.

**Rollback**, at any time in the 30 days: disable the schedules, then `docker compose start scheduler`.
The local sync continues from its cursors and re-fetches recent days. Data that exists only on AWS
needs a reverse dump first: today only manual thresholds; once phase 3 stores plans, plans too.

**Full removal:** `purge-data`, lift `prevent_destroy` and deletion protection, `terraform destroy`,
delete snapshots, schedule the KMS key for deletion. Purged rows stay in RDS backups until they age
out (7 days). B2 is unaffected.

## Security review per stage

| PR | Review | SECURITY.md updates |
|---|---|---|
| B0 | No credentials in CI, zizmor clean, deny rules for state and plan files, new tools justified | Automated checks rows |
| B1 | Root MFA, no IAM users, member root credentials removed, SSO MFA, OIDC `sub`, organization trail unchangeable from members, Region SCP tested, budget alerts fire | Trust boundaries (AWS, OIDC); cloud credential incident steps; quarterly SSO review |
| B2 | SCP check output; `none` read back on both APIs in all six Regions; global inference denied; invocation logging denied; no Bedrock in the management account | Bedrock controls, CSAM exception, six processing Regions, monthly retention read |
| B3a, B3b | Keys validated; errors carry no keys or bodies; leak test covers S3; master credentials as `SecretStr` | Blob controls, new dependency, RDS CA |
| B3c | RDS private and encrypted; S3 TLS-only and private; KMS key policy; ECR private (FIT SDK license); secrets empty in Terraform; state checked for secrets | Encryption at rest and in transit; backups vs `purge-data`; 90-day check covers ECR |
| B3d | Roles separated per task; no inbound rules; read-only root filesystem; Exec off; logs checked for values; alerts and restore tested | Scheduler and network threats; key rotation through Secrets Manager. Closes the AWS deployment review. |

## Open checks

Tracked in [OPEN_QUESTIONS](../OPEN_QUESTIONS.md), items 8 and 9: `db.t4g.micro` orderability, the OIDC
subject, the Bedrock API key action, `id-token` on fork PRs, TFLint ruleset pinning and prices.

## Sources

Checked 2026-11-01.

- Bedrock: [data retention](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html),
  [zero-retention SCP blog](https://aws.amazon.com/blogs/security/enforce-zero-data-retention-on-amazon-bedrock-with-bedrock-projects-and-service-control-policies/),
  [abuse detection](https://docs.aws.amazon.com/bedrock/latest/userguide/abuse-detection.html),
  [model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html),
  [inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html),
  [global inference](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html),
  model cards for [Sonnet 5.5](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5-5.html),
  [Opus 5.5](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5-5.html)
  and [Opus 4.8](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-4-8.html).
- Organizations and IAM: [SCPs](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html),
  [root user and centralized root access](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_root-user.html),
  [OIDC roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html),
  [organization trail](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/creating-trail-organization.html).
- RDS and ECS: [PostgreSQL release calendar](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-release-calendar.html),
  [TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html),
  [Secrets Manager integration](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-secrets-manager.html),
  [ECS secrets](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html),
  [task events](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs_task_events.html),
  [Scheduler targets](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-targets-templated.html).
- Terraform and tools: [S3 backend](https://developer.hashicorp.com/terraform/language/backend/s3),
  [lock file](https://developer.hashicorp.com/terraform/language/files/dependency-lock),
  [ephemeral values](https://developer.hashicorp.com/terraform/language/manage-sensitive-data/ephemeral),
  [Terraform license](https://github.com/hashicorp/terraform/blob/main/LICENSE),
  [OpenTofu encryption](https://opentofu.org/docs/language/state/encryption/),
  [Trivy Terraform coverage](https://trivy.dev/docs/latest/guide/coverage/iac/terraform/).
- GitHub: [OIDC in AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws).
- Prices: AWS Price List bulk files for `eu-north-1` and `eu-central-1`
  (`pricing.us-east-1.amazonaws.com/offers/v1.0/aws/…`); [Budgets](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/),
  [CloudTrail](https://aws.amazon.com/cloudtrail/pricing/), [Free Tier terms](https://aws.amazon.com/free/terms/).
