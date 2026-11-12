# AWS build notes (track B)

What was built for each AWS stage, choices made where
[ADR 0008](../../decisions/0008-cloud-deployment.md) and [docs/aws-deployment.md](../aws-deployment.md)
left room, and what later stages must provide. The ADR holds the decisions; this file holds how
they were carried out ([ADR 0017](../../decisions/0017-build-notes-out-of-adrs.md)). Each stage PR
adds its own section. No account IDs, ARNs or state: this repository is public.

## B0 implementation (2026-11-03, versions bumped 2026-11-06)

Release dates rechecked with
`gh api` on 2026-11-06.

- **Versions.** Terraform 1.17.1 (`required_version = "~> 1.17.0"`), `hashicorp/aws` exactly 6.71.0
  (6.72.0 is under 7 days old), TFLint 0.65.0, TFLint AWS ruleset 0.50.0. No cooldown exception
  was needed.
- **TFLint AWS ruleset: pinned, so not core-only** (OPEN_QUESTIONS 9). `gh attestation verify`
  showed the release's `checksums.txt` was signed by the ruleset repo's `release.yml` at tag
  v0.50.0. CI downloads the Linux zip and checks the SHA-256 from that file, instead of
  `tflint --init`, which verifies the attestation but pins no hash and needs a GitHub token.
  `deep_check` (AWS API calls) is off.
- **Trivy custom checks.** Built-in checks already cover public RDS and open inbound rules, so B0
  adds only GA001: every `aws` provider sets an explicit `eu-*` region (Rego in `infra/policy/`,
  run through `--raw-config-scanners terraform`). CI self-tests it against a non-EU provider each
  run. `--skip-check-update` uses the checks embedded in the pinned image rather than downloading
  the latest bundle, so check changes arrive with Trivy bumps.
- **Account guard.** Every root sets `allowed_account_ids` from a validated `account_id` variable;
  the example tfvars placeholder fails validation if left unfilled.
- **Script name.** `infra/scripts/plan_summary.py` (not `plan-summary`) so ruff, pyright strict and
  pytest cover it; standard library only.
- **State bootstrap.** `org` and `account` start on local state through a gitignored
  `backend_override.tf` and migrate to their bucket in B1.
- **Claude deny rules.** Reading state, plans, tfvars, `backend.hcl` and `~/.aws`; running
  `terraform` apply, destroy, plan, refresh, state, show, output, console, import and
  force-unlock. They match command text, so they back up the rule rather than enforce it.

## B1 implementation (2026-11-09)

`infra/org` and `infra/account`; nothing from B2 or B3. AWS and
GitHub facts checked on 2026-11-09. Versions unchanged from B0 (Terraform 1.17.1, `hashicorp/aws`
6.71.0 with the committed lock files); no modules. The owner's steps:
[docs/aws-b1-checklist.md](../aws-b1-checklist.md).

- **Done by hand, then imported or looked up.** The Identity Center organization instance can only
  be enabled in the console (`CreateInstance` makes account instances and is rejected in a
  management account), and its MFA settings have no API. The console creates the organization
  along with it, so Terraform **imports** the organization rather than creating it. The one user is
  created by hand and looked up by user name. A temporary `Bootstrap` permission set carries the
  first apply and is deleted afterwards; Terraform owns every lasting permission set and assignment.
- **Centralized root access** (`RootCredentialsManagement`, `RootSessions`) is enabled before the
  member account is created, so that account never has root credentials (IAM: centralized root
  access). The owner confirms it with `sts assume-root` scoped to `IAMAuditRootUserCredentials`.
  `OrganizationAccountAccessRole` stays as the way back in if Identity Center fails; it grants
  nothing `WorkloadAdmin` doesn't.
- **Trusted access** is exactly CloudTrail, IAM and Identity Center. SCPs are enabled.
- **Permission sets.** `ManagementAdmin`: `AdministratorAccess` plus an inline deny of `bedrock:*`
  and `bedrock-mantle:*`, a guardrail against accidental use rather than a boundary (an admin can
  still create a role without it). `WorkloadAdmin`: `AdministratorAccess` in the member account. One-hour
  sessions, because a role session outlives a revoked sign-in until it expires (Identity Center:
  authentication sessions). B2 adds `BedrockUser`.
- **Budget.** $25 a month on cost, gross of credits and refunds, alerts at 50, 80 and 100 % of
  actual and forecast. The action sits on the same budget at an **absolute $50 of actual spend**:
  automatic, it attaches `budget-deny-invoke` (`bedrock:Invoke*`, `StartAsyncInvoke`,
  `CreateModelInvocationJob`, `bedrock-mantle:*`) to the `workload` OU. Its role trusts
  `budgets.amazonaws.com` for this budget only (`aws:SourceAccount`, and `aws:SourceArn` matching
  the budget or `budget/<name>/action/*`: AWS doesn't document which one Budgets presents, so the
  owner fires the action once with a tiny budget, checklist section 8) and may attach or detach
  only that policy on that OU, narrower than AWS's example. The owner reverses the action by hand.
- **Organization trail.** Multi-Region, global service events, log file validation. Its bucket
  keeps log files a year and overwritten or deleted versions 90 days; the bucket policy is AWS's
  organization-trail policy with `aws:SourceArn` pinned to this trail. Not sent to CloudWatch
  Logs, not KMS-encrypted (Trivy exceptions, reasons inline).
- **State and buckets.** All three buckets: public access blocked, ACLs disabled, versioned,
  TLS only, `prevent_destroy`, no server access logging (the trail records management calls).
  The management account's two use **SSE-S3**: one customer-managed key is planned, in the member
  account, and that state holds IDs, ARNs and two emails but no secrets. The member state bucket
  uses SSE-KMS with a bucket key, and its policy refuses writes asking for other encryption,
  another key, or `aws:kms` without a key ID (which S3 would encrypt with `aws/s3`).
- **KMS key** (`alias/garminagent`): rotation on, 30-day deletion window, policy delegating to IAM
  only. B3c adds the service statements RDS, S3 and Secrets Manager need.
- **GitHub OIDC.** Provider without a thumbprint (IAM trusts GitHub's CA). `gha-ecr-push` has no
  policy, one-hour sessions, and `StringEquals` on the audience and on one subject:
  `repo:OWNER@OWNER_ID/REPO@REPO_ID:environment:ecr-push`. GitHub documents that ID format for
  repositories created after 15 July 2026 with a branch example; the environment form is inferred,
  and B3c's first run confirms it. The IDs are public and come from tfvars. **B3c must** keep the
  push job off `pull_request_target` and `workflow_run`: both run on `main`, so they would pass
  the environment's main-only rule with a fork's influence.
- **Access Analyzer**: external access, account zone (free).
- **No personal data in plans.** Emails and the SSO user name are `sensitive` variables;
  `for_each` keys are fixed words (`state`, `trail`).
- **Tests**: mocked provider with synthetic values; runs use `command = apply` against the mock to
  assert computed policies: SCP actions, the permission set deny, budget thresholds and action
  target, the budget role's trust conditions, bucket controls, the trail's flags and
  `aws:SourceArn`, the KMS key policy, the OIDC trust (provider, action, exact subject, audience,
  no `StringLike`), and rejection of each example file's literal placeholders. "No policy on
  `gha-ecr-push`" is checked in review, not by a test.
- **Review** ([docs/reviews/b1-review.md](../reviews/b1-review.md), 2026-11-12): ready after
  fixes; M1–M3 and L1–L5 fixed before the PR.

## B3b implementation (2026-11-07)

App code only; no `infra/` change, no AWS resource, no
migration. Release dates checked on PyPI on 2026-11-07.

- **Dependency.** `boto3` 1.43.123 (2026-10-30; 1.43.124–126 are under 7 days old), with `botocore`
  1.43.123, `s3transfer` 0.19.3 (Apache-2.0) and `jmespath` 1.1.0 (MIT). Maintained by AWS, released
  every weekday. Why: AWS's own SDK does SigV4 signing, the credential chain (the ECS task role)
  and retries; our own signer would be more code to get wrong. Dev only: `types-boto3[s3]` 1.43.123
  (MIT) for pyright strict. No cooldown exception. The image grows from 309 to 364 MB, mostly
  botocore's service models (31 MB).
- **S3 store** (`storage/s3.py`) as planned, plus: only `NoSuchKey` counts as a missing blob (a
  missing bucket is also a 404 and must not mark files `invalid`); other failures raise
  `BlobStoreError`, an `OSError`, so callers treat both backends alike; standard retry mode, 5
  attempts. `purge-data` now exits 1 without a traceback if deleting blobs fails (re-run it).
- **What B3c and B3d must provide.** The task role needs `s3:ListBucket` on the bucket as well as
  get, put and delete on its objects (without it a missing key is 403, not 404, and `delete_prefix`
  can't list), plus the KMS key's `GenerateDataKey` and `Decrypt`. Task definitions set
  `AWS_REGION`, `BLOB_BACKEND=s3`, `BLOB_S3_BUCKET` and `DB_REQUIRE_VERIFY_FULL=true`. The
  `db-bootstrap` task maps the RDS-managed secret's JSON keys to `DB_MASTER_USERNAME` and
  `DB_MASTER_PASSWORD` (`<secret ARN>:username::` and `:password::`). URLs need
  `?sslmode=verify-full`; the image sets `PGSSLROOTCERT` to the bundle, so `sslrootcert` is optional.
- **`db-bootstrap`** (`storage/bootstrap.py`). The owner doesn't become the database owner: the RDS
  master isn't a superuser and, from PostgreSQL 16, can't act as roles it creates without granting
  itself membership first. Instead the owner gets CONNECT and `USAGE, CREATE ON SCHEMA public WITH
  GRANT OPTION` (the option lets migration 0001 grant the app role USAGE, unchanged). The app role
  gets CONNECT; PUBLIC loses CONNECT, as in the compose init script. Both roles are `NOSUPERUSER
  NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`. Passwords go to the server only as SCRAM
  verifiers computed by libpq on the client, so they can't reach server logs. Everything runs in
  one transaction and the grants are checked afterwards (GRANT and REVOKE only warn when not
  allowed). It refuses when the master is also the owner or app user, so it doesn't run against the
  compose stack itself, where the owner is the superuser; the compose init script stays the local
  bootstrap, and the integration tests run `db-bootstrap` on the compose Postgres image with an
  RDS-like master (CREATEROLE, CREATEDB, not a superuser), then all migrations as its owner. M6f's
  `garminagent_agent` is a marked extension point (a third role of kind `member`, name and grants
  from M6f's `garminagent.storage` module, password from `AGENT_DATABASE_URL`); not built.
- **TLS guard.** `warn_if_insecure` became `check_tls`: with `DB_REQUIRE_VERIFY_FULL`, a non-local
  host without `sslmode=verify-full` raises before connecting, from `make_engine` (app,
  `current_revision`, `db-bootstrap`) and the Alembic env (migrations). `make_engine` takes the flag
  explicitly, so `db-bootstrap` checks the settings it was given rather than the cached ones.
- **RDS CA bundle.** The global bundle, at `docker/certs/rds-global-bundle.pem`, stored without
  line-ending conversion (`*.pem -text`) so its SHA-256 (SECURITY.md, checked by a test) holds on
  every checkout. Root-owned and read-only in the image.
