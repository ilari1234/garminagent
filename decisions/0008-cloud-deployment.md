# 0008: AWS via Terraform, in stages

**Status:** Accepted. Direction: AWS, Terraform, local until then, staged (owner, 2026-10-24 and
2026-10-29). Design: produced by the AWS planning session on 2026-11-01, accepted by the owner on
2026-11-03 with the defaults below. Detail: [docs/aws-deployment.md](../docs/aws-deployment.md).
Vendor facts and prices are as of 2026-11-01. B0 implemented on branch `infra-b0` (2026-11-03): see
*B0 implementation* below. B3b implemented 2026-11-07: see *B3b implementation*.

## Context

Phase 1 runs locally by the owner's choice, built to move to AWS with configuration only. Phase 3
needs Bedrock with an enforceable retention rule, but doesn't need the sync running in the cloud.

## Decision

**Stages.** One PR each; the owner runs every `apply`.

| PR | Delivers | Owner applies |
|---|---|---|
| B0 tooling | `infra/` skeleton, lock files, CI checks, gitignore and Claude deny rules | none |
| B1 foundation | Organization with a `workload` OU and one member account, SSO with MFA, budgets, organization trail, state buckets, GitHub OIDC role with no permissions yet | `org`, `account` |
| B2 Bedrock | Retention SCP, Region SCP, SSO role, script that sets and verifies retention | `org` |
| B3a seam | `make_blob_store` factory only | none |
| B3b app code | S3 `BlobStore`, `db-bootstrap`, RDS CA bundle, TLS guard | none |
| B3c data and network | VPC, RDS, S3, KMS, secrets, ECR, image push | `workload` |
| B3d compute and cutover | ECS tasks, schedules, alerts, operations guide | `workload` |

**Key choices**

- Region `eu-north-1`. Layout `infra/org`, `infra/account`, `infra/workload`, `infra/scripts`.
- No IAM users or access keys: SSO for people, OIDC for CI. Member root credentials removed.
- S3 state with lockfile locking. Secrets stay out of state: RDS manages its master password;
  Terraform creates empty secrets and the owner fills them from a terminal.
- **Public CI runs only credential-free checks.** No `plan` in CI, because its output contains ARNs
  and account IDs and CI logs are public. `plan-summary` prints only addresses and actions, which
  are safe to share. The sole CI-to-AWS link is an ECR push role limited to one repository.
- No third-party modules, because the lock file doesn't pin them.
- Network: tasks in public subnets with public IPs and no inbound rules; RDS private; S3 gateway
  endpoint; no NAT.
- Migration by **re-sync from Garmin**, not by moving a dump of health data.
- About $18/month in Stockholm plus Bedrock tokens; B1 and B2 alone about $1–2. Budget $25, with a
  deny action at $50.

**Bedrock retention (owner, 2026-11-03).** It isn't documented whether a cross-Region `eu.` request
follows the source Region's retention setting or the destination's. So `none` is set in **all six**
destination Regions of the `eu.` profile from Stockholm. Milan and Spain are opt-in Regions, enabled
in the member account only for this; the Region SCP denies everything else there. The control-plane
and mantle retention APIs are treated as two settings: the SCP covers both and the script writes and
reads both. No mantle projects are used. Fallback: Opus 4.8 on `bedrock-mantle` in-Region in Stockholm.

**Database class.** `db.t4g.micro`, unless the orderability check or the real-data sample volume
(OPEN_QUESTIONS 3, 8) says otherwise; then `db.t4g.small`.

**Agent role.** `garminagent_agent` comes with M6f (owner, 2026-11-03); `db-bootstrap` creates it on RDS,
taking the name from M6f's `garminagent.storage` module and the password from a third URL secret,
`AGENT_DATABASE_URL` (+$0.40/month).

**B0 implementation (2026-11-03, versions bumped 2026-11-06).** Release dates rechecked with
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

**B3b implementation (2026-11-07).** App code only; no `infra/` change, no AWS resource, no
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

## Alternatives considered

- **OpenTofu.** MPL-2.0 and built-in state encryption. Not chosen: the owner asked for Terraform,
  BUSL 1.1 doesn't limit this use, and state is designed to hold no secrets. Switching later is
  about one PR if Terraform-only features are avoided.
- **Deploying everything before phase 3.** Rejected: costs more for longer; agents can run from the
  owner's machine against Bedrock.
- **`plan` in CI.** Rejected, see above. This gives up automatic plan previews on PRs.
- **NAT gateway** ($33.58/month plus data) and interface endpoints. Rejected for cost. A NAT wouldn't
  stop a compromised dependency reaching any host on 443; only a filtering proxy would.
- **Multi-AZ RDS, `db.t4g.small`, ARM, stopping RDS between runs, Control Tower, Config, Security
  Hub, GuardDuty.** Rejected for cost or complexity at single-user scale, knowingly giving up
  continuous compliance and threat detection.
- **Versioned blob bucket.** Rejected: versioning would keep data `purge-data` is meant to delete.
- **Restoring a dump.** Possible but untested; keeps history that re-sync loses.

## Consequences

- SCPs don't bind the management account, so Bedrock stays out of it. A management admin can still
  detach an SCP; the trail records that.
- The Bedrock retention setting has no resource in the AWS provider (as of v6.71.0), so a script sets
  and verifies it, before any prompt is sent.
- Track B needs small edits outside `infra/`: workflows, Dependabot, `.gitignore`,
  `.claude/settings.json` (B0); app code, Dockerfile, `pyproject.toml`, `uv.lock` (B3b); `ci.yml`
  (B3c). B3a, code only, may land before B1 (owner, 2026-11-03), and should land before M6a touches
  the code next to it, and B3b rebases after any open M6 PR.
- M6f's agent role must also be created by `db-bootstrap` (ADR 0013).
- Purged rows stay in RDS backups until they age out; SECURITY.md must state this.
- Two opt-in Regions are enabled solely to pin their retention setting; the Region SCP keeps them
  otherwise unusable.
- Remaining checks before implementation are in OPEN_QUESTIONS 8 and 9.
