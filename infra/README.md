# infra

Terraform for the AWS deployment. Plan and stages: [docs/aws-deployment.md](../docs/aws-deployment.md);
decision: [ADR 0008](../decisions/0008-cloud-deployment.md). Stage B0 (this skeleton) creates no
resources.

| Root | Account | Holds |
|---|---|---|
| `org/` | management | organization, OU, SCPs, SSO, budgets, organization trail, own state bucket |
| `account/` | member | state bucket and KMS key, GitHub OIDC provider and role, Access Analyzer |
| `workload/` | member | VPC, RDS, S3, ECR, secrets, ECS, schedules, alarms |
| `scripts/` | | `plan_summary.py`; `bedrock-retention` comes with B2 |
| `policy/` | | custom Trivy checks (Rego) |

Each root pins Terraform `~> 1.17.0` and `hashicorp/aws` exactly, has an S3 backend with
`use_lockfile = true` and partial configuration, a provider fixed to `eu-north-1` with default
tags and `allowed_account_ids`, and a mocked-provider test under `tests/`.

## Where things run

The repo and its CI logs are public. **CI** (`.github/workflows/infra.yml`) has no AWS credentials
and runs only `fmt`, `init -backend=false`, `validate`, `test`, TFLint and a Trivy config scan.
**`plan`, `apply` and any state access happen only in the owner's terminal.** Claude Code is
denied reading state, plan, tfvars and `backend.hcl` files and running those commands
(`.claude/settings.json`); those deny rules match command text, so they back up the rule rather
than enforce it.

## The owner's flow

```bash
cd infra/<root>
cp backend.hcl.example backend.hcl            # gitignored; fill in
cp terraform.tfvars.example terraform.tfvars  # gitignored; fill in
terraform init -backend-config=backend.hcl
terraform plan -out=plan.tfplan
terraform show -json plan.tfplan | python ../scripts/plan_summary.py   # safe to share
terraform apply plan.tfplan
```

`plan_summary.py` prints resource addresses and actions only, never values. Share its output, not
the plan.

**Bootstrapping a state bucket** (`org` and `account`, B1): the root that creates its own bucket
starts on local state, through a gitignored `backend_override.tf` containing
`terraform { backend "local" {} }`, then moves to the bucket with
`terraform init -migrate-state -backend-config=backend.hcl` after deleting the override. B1
documents the exact steps.

## Conventions

- **No third-party modules**: the lock file pins providers only.
- **No secrets in code, tfvars or state.** RDS manages its master password; secrets are created
  empty and filled from a terminal. Nothing credential-shaped is committed, not even fake values.
- **Never key a resource by an account ID, email or secret.** `for_each` keys are part of the
  address, and addresses are what `plan_summary.py` prints.
- **Real `*.tfvars`, `backend.hcl`, state and plans stay out of git** (`.gitignore`). Example
  files hold placeholders that fail validation if left unfilled.
- Every provider block sets an EU `region` explicitly (Trivy check `GA001` in `policy/`).

## Running the checks locally

From the repo root, with Docker (Git Bash on Windows needs `MSYS_NO_PATHCONV=1`). The image
digests are the ones in `infra.yml`.

```bash
TF=hashicorp/terraform:1.17.1@sha256:a5d5a5a892c98f60fcbfa81eac2a40fc19e7b3fa7719bccae10ff6111d22524a
docker run --rm -e CHECKPOINT_DISABLE=1 -v "$PWD/infra:/infra" -w /infra "$TF" fmt -recursive
docker run --rm -e CHECKPOINT_DISABLE=1 -v "$PWD/infra:/infra" -w /infra/org "$TF" init -backend=false -lockfile=readonly
docker run --rm -e CHECKPOINT_DISABLE=1 -v "$PWD/infra:/infra" -w /infra/org "$TF" test
```

TFLint needs the AWS ruleset binary in a plugin directory; follow the TFLint job in `infra.yml`.

## Updating versions

- **AWS provider:** Dependabot (`terraform` ecosystem, 7-day cooldown) bumps the version and the
  lock files. To do it by hand, change the version in all three `versions.tf`, then in each root
  run `terraform providers lock -platform=linux_amd64 -platform=windows_amd64 -platform=darwin_arm64`.
- **Terraform, TFLint, the TFLint AWS ruleset:** monthly, by hand (SECURITY.md routine), with
  releases at least 7 days old. For Terraform, change the image digest in `infra.yml` and, for a
  minor version, `required_version` in all three roots. For the ruleset, run
  `gh attestation verify checksums.txt -R terraform-linters/tflint-ruleset-aws` on the new
  release's `checksums.txt`, check the signer is that repo's `release.yml` at the new tag, and
  update `RULESET_URL` and `RULESET_SHA256` together.
- **Trivy and cosign:** shared with `ci.yml`; bump both workflows together.
