# AWS B1: the owner's checklist

Everything B1 needs that Terraform can't do, in order, plus the two first applies and the checks
afterwards. Design: [ADR 0008](../decisions/0008-cloud-deployment.md), [build notes](build-notes/aws.md#b1-implementation-2026-11-09), and
[docs/aws-deployment.md](aws-deployment.md#b1-foundation). AWS and GitHub facts here were checked
on **2026-11-09**; recheck anything that fails.

**Rules for every step.** Run commands in your own terminal. Never paste account IDs, ARNs,
emails, organization IDs or `terraform` output into chat, issues or PRs; share only
`plan_summary.py` output. `terraform.tfvars`, `backend.hcl`, `backend_override.tf`, state and
plan files are gitignored: keep it that way, and delete local state once it has moved to S3.

**Cost.** About $1–2 a month: the KMS key ($1), a few cents of S3, the organization trail's
first copy of management events (free), one action-enabled budget (the first two are free),
Identity Center and the external access analyzer (free).

## 0. Before you start

- [ ] AWS CLI v2, Terraform 1.17.x (CI runs 1.17.1), Python 3 for `plan_summary.py`, `gh`.
- [ ] Three globally unique S3 bucket names: org state, organization trail, member state. No
      personal data in them.
- [ ] An email address for the member account's root user that no AWS account uses yet
      (plus-addressing works). It only receives AWS notices: the account will have no root
      password.
- [ ] An address for budget alerts.

## 1. Management account and its root user (console)

- [ ] Create the AWS account that becomes the management account. Creating an organization
      upgrades a Free plan account to a paid one; don't count on credits.
- [ ] Sign in as root and register MFA, ideally two passkeys or security keys (one kept offline).
      AWS enforces root MFA, but adding the device is up to you (at sign-up or when prompted).
- [ ] Confirm the root user has **no access keys** (IAM → Security credentials). Never create any.
- [ ] Optional: Account → Alternate contacts → Security, so AWS can reach you about abuse.

## 2. Organization and IAM Identity Center (console, as root, the last time root is used)

Identity Center's organization instance can only be enabled in the console: `CreateInstance`
makes account instances and is rejected in a management account. MFA settings have no API.

- [ ] Switch the console to **Europe (Stockholm) `eu-north-1`**. The instance's Region is where
      Identity Center keeps its data.
- [ ] Open IAM Identity Center → **Enable** → organization instance. If no organization exists,
      the console offers to create one with all features: accept. (Otherwise create it first in
      AWS Organizations.)
- [ ] Identity source: keep the **Identity Center directory**. No external identity provider.
- [ ] Settings → Authentication → Multi-factor authentication → Configure:
  - prompt **every time they sign in (always-on)**, not context-aware: with context-aware a
    "trusted device" skips MFA even when sign-in without it is blocked;
  - MFA types: security keys and built-in authenticators (authenticator apps too if you like);
  - if a user has no device: **require them to register an MFA device at sign in**.
- [ ] Users → Add user: the one user. Its user name goes into `sso_user_name`. Accept the email
      invitation, set a password, sign in and register MFA (two devices).
- [ ] Permission sets → create a temporary one called **`Bootstrap`**: AWS managed
      `AdministratorAccess`, session duration 1 hour. Assign your user to the **management
      account** with it. It exists only for the first apply (step 4 deletes it).
- [ ] Sign out of root. Store the root password and MFA devices safely; root isn't needed again.
- [ ] `aws configure sso` → a profile for the management account with `Bootstrap`, here called
      `garminagent-bootstrap` (SSO start URL from the Identity Center dashboard, SSO Region
      `eu-north-1`). `aws sts get-caller-identity --profile garminagent-bootstrap` should work;
      don't paste its output.

## 3. `infra/org`: first apply, on local state

The organization exists already, so Terraform imports it instead of creating it.

```bash
cd infra/org
cp terraform.tfvars.example terraform.tfvars   # fill in
cp backend.hcl.example backend.hcl             # fill in (bucket = state_bucket_name)
printf 'terraform {\n  backend "local" {}\n}\n' > backend_override.tf
export AWS_PROFILE=garminagent-bootstrap
terraform init
terraform import aws_organizations_organization.this "$(aws organizations describe-organization --query Organization.Id --output text)"
terraform plan -out=plan.tfplan
terraform show -json plan.tfplan | python ../scripts/plan_summary.py
```

- [ ] Read the summary. Expected: `aws_organizations_organization.this` **update** (SCPs and
      trusted access), everything else **create**: the OU, centralized root access, the member
      account, two permission sets with their policy and assignments, the budget with its SCP,
      role and action, two buckets with their settings and policies, the trail. No delete or
      replace. If the organization update would remove a trusted-access principal you rely on,
      stop and ask.
- [ ] `terraform apply plan.tfplan`. Creating the member account takes a few minutes.

## 4. Switch to Terraform's permission sets

- [ ] The access portal now shows `ManagementAdmin` (management account) and `WorkloadAdmin`
      (member account). `aws configure sso` → profiles `garminagent-mgmt` and
      `garminagent-workload`.
- [ ] Bedrock is denied in the management account:
      `aws bedrock list-foundation-models --region eu-north-1 --profile garminagent-mgmt` fails
      with an explicit deny.
- [ ] Delete the `Bootstrap` permission set: remove its account assignment, then delete it. Remove
      the `garminagent-bootstrap` profile from `~/.aws/config`.

## 5. Move `infra/org` state into its bucket

```bash
export AWS_PROFILE=garminagent-mgmt
rm backend_override.tf
terraform init -migrate-state -backend-config=backend.hcl
terraform plan            # expect: No changes
rm -f terraform.tfstate terraform.tfstate.backup plan.tfplan
```

- [ ] `No changes`, and the local state files are gone (they held IDs and emails).

## 6. Member account root credentials

With centralized root access on before the account was created, the member account has **no
root credentials**. Confirm it with a privileged session scoped to auditing (Bash; `sts
assume-root` needs a Regional endpoint):

```bash
export AWS_PROFILE=garminagent-mgmt
name=garminagent-workload   # or your workload_account_name, if you changed it
member=$(aws organizations list-accounts --query "Accounts[?Name=='$name'].Id" --output text)
[[ "$member" =~ ^[0-9]{12}$ ]] || { echo "member account not found: check the name"; return 1 2>/dev/null || exit 1; }
read -r AK SK ST <<< "$(aws sts assume-root --region eu-north-1 --target-principal "$member" \
  --task-policy-arn arn=arn:aws:iam::aws:policy/root-task/IAMAuditRootUserCredentials \
  --duration-seconds 900 --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text)"
as_root() { env -u AWS_PROFILE AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_SESSION_TOKEN="$ST" aws "$@"; }
as_root iam get-login-profile            # expect NoSuchEntity
as_root iam list-access-keys             # expect an empty list
as_root iam list-mfa-devices             # expect an empty list
as_root iam list-signing-certificates    # expect an empty list
unset AK SK ST member name
```

- [ ] All four empty. If anything exists, repeat with the task policy
      `IAMDeleteRootUserCredentials` and run `iam delete-login-profile`, `iam delete-access-key`,
      `iam deactivate-mfa-device` or `iam delete-signing-certificate` as needed, then audit again.
      (Console alternative: IAM → Root access management → the account → Take privileged action.)

## 7. `infra/account`: first apply, then move its state

```bash
cd ../account
cp terraform.tfvars.example terraform.tfvars   # fill in; account_id is the member account
gh api repos/ilari1234/garminagent --jq '{owner: .owner.login, owner_id: .owner.id, repo: .name, repo_id: .id}'
cp backend.hcl.example backend.hcl             # bucket now; kms_key_id after the apply
printf 'terraform {\n  backend "local" {}\n}\n' > backend_override.tf
export AWS_PROFILE=garminagent-workload
terraform init
terraform plan -out=plan.tfplan
terraform show -json plan.tfplan | python ../scripts/plan_summary.py
```

- [ ] Expected: **create** only. The KMS key and alias, the state bucket with its settings and
      policy, the OIDC provider, `aws_iam_role.gha_ecr_push`, the access analyzer. No policy
      attached to the role.
- [ ] `terraform apply plan.tfplan`.
- [ ] Put the key's ARN into `backend.hcl` without printing it anywhere else:
      `aws kms describe-key --key-id alias/garminagent --query KeyMetadata.Arn --output text`.

```bash
rm backend_override.tf
terraform init -migrate-state -backend-config=backend.hcl
terraform plan            # expect: No changes
rm -f terraform.tfstate terraform.tfstate.backup plan.tfplan
```

- [ ] `No changes`; local state files gone. `infra/workload/backend.hcl` (B3c) uses the same
      bucket and key.

## 8. Checks for the B1 security review

Answers go into the PR or SECURITY.md as yes/no, never as output.

- [ ] Management root: `aws iam get-account-summary --profile garminagent-mgmt` shows
      `AccountMFAEnabled` 1 and `AccountAccessKeysPresent` 0.
- [ ] No IAM users: `aws iam list-users` is empty with both profiles.
- [ ] SSO MFA: signing in from a private browser window asks for MFA.
- [ ] Trail logging: `aws cloudtrail get-trail-status --name garminagent-org --profile
      garminagent-mgmt` shows `IsLogging` true. After about 15 minutes the trail bucket has
      objects for both accounts.
- [ ] Members can't touch the trail. **Both commands carry `--profile garminagent-workload`:**
      with management credentials the same command really stops the trail.

      ```bash
      trail=$(aws cloudtrail describe-trails --profile garminagent-workload --query 'trailList[0].TrailARN' --output text)
      aws cloudtrail stop-logging --name "$trail" --profile garminagent-workload   # expect AccessDenied or similar
      aws cloudtrail get-trail-status --name garminagent-org --profile garminagent-mgmt --query IsLogging   # expect true
      ```

      If `IsLogging` is ever false, run `aws cloudtrail start-logging --name garminagent-org
      --profile garminagent-mgmt` at once and note the gap.
- [ ] Budget: the Budgets console shows `garminagent-monthly` with six alerts and one action
      (`APPLY_SCP_POLICY`, automatic, $50 actual) in standby.
- [ ] Budget alerts and the action fire (required: the action is the backstop, and which ARN
      Budgets presents to its role is checked only here). While nothing runs in the workload
      account, set `budget_limit_usd = 0.01` and `budget_action_usd = 0.02` in `infra/org`'s
      tfvars and apply; the KMS key's monthly fee passes both. Within a day:
      - the alert email arrives;
      - the action shows as executed, and
        `aws organizations list-policies-for-target --target-id <workload OU id> --filter SERVICE_CONTROL_POLICY --profile garminagent-mgmt`
        lists `budget-deny-invoke`. If the action failed instead, its error names the cause:
        fix the role's trust in a PR.

      Then reverse the action in the Budgets console, confirm the policy is no longer listed,
      restore the two values and apply.
- [ ] Access Analyzer (`garminagent-workload`): open findings in the console or with
      `aws accessanalyzer list-findings-v2`. A finding for `gha-ecr-push` trusting GitHub's OIDC
      provider is expected: archive it with a note. Investigate anything else.

## 9. At B3c's first CI run: confirm the OIDC subject

The role accepts exactly one token subject:
`repo:OWNER@OWNER_ID/REPO@REPO_ID:environment:ecr-push`. GitHub documents this ID format for
repositories created after 15 July 2026 (this one was created in October 2026) and gives a branch
example; the environment form is inferred from it, so the first run confirms it.

- [ ] Before the run: the `ecr-push` environment allows deployments from `main` only (B3c sets
      this up).
- [ ] If the job's credentials step succeeds, the subject matched. Nothing else to do.
- [ ] If it fails with `Not authorized to perform sts:AssumeRoleWithWebIdentity`: **don't** print
      token claims in the public log. In your terminal, look at the failed call in the member
      account's CloudTrail event history:
      `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity --max-results 5 --region eu-north-1 --profile garminagent-workload`.
      Its `userIdentity` shows the subject GitHub sent. Fix the `github_*` variables (or, if the
      format differs, the trust policy in a PR), apply, and re-run.

## Undoing B1

Key resources have `prevent_destroy`, and the member account is never closed by Terraform
(`close_on_deletion = false`). Removing B1 is a deliberate, manual job: lift `prevent_destroy`,
empty and delete the buckets, schedule the KMS key for deletion, close the member account in
Organizations, then delete the organization. Ask before starting.
