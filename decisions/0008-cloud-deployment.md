# 0008: AWS via Terraform, in stages

**Status:** Accepted. Direction: AWS, Terraform, local until then, staged (owner, 2026-10-24 and
2026-10-29). Design: produced by the AWS planning session on 2026-11-01, accepted by the owner on
2026-11-03 with the defaults below. Detail: [docs/aws-deployment.md](../docs/aws-deployment.md).
Vendor facts and prices are as of 2026-11-01.

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
  (B3c). B3a should land before M6a touches the code next to it, and B3b rebases after any open M6 PR.
- M6f's agent role must also be created by `db-bootstrap` (ADR 0013).
- Purged rows stay in RDS backups until they age out; SECURITY.md must state this.
- Two opt-in Regions are enabled solely to pin their retention setting; the Region SCP keeps them
  otherwise unusable.
- Remaining checks before implementation are in OPEN_QUESTIONS 8 and 9.
