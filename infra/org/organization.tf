# The organization is created by hand in the console (with the Identity Center instance, which
# needs it) and imported before the first apply: docs/aws-b1-checklist.md.
resource "aws_organizations_organization" "this" {
  feature_set = "ALL"

  # B2's Bedrock SCPs and the budget action's deny-invoke SCP need this policy type.
  enabled_policy_types = ["SERVICE_CONTROL_POLICY"]

  # Trusted access, and nothing else. Anything enabled by hand and missing here is removed on
  # apply, so read the import plan for removals.
  # - cloudtrail: the organization trail.
  # - iam: centralized root access for member accounts.
  # - sso: the IAM Identity Center organization instance.
  aws_service_access_principals = [
    "cloudtrail.amazonaws.com",
    "iam.amazonaws.com",
    "sso.amazonaws.com",
  ]

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_organizations_organizational_unit" "workload" {
  name      = "workload"
  parent_id = aws_organizations_organization.this.roots[0].id

  lifecycle {
    prevent_destroy = true
  }
}

# Lets the management account delete member root credentials and run scoped privileged tasks
# (sts:AssumeRoot). Accounts created after this have no root credentials at all, so it must
# exist before the member account.
resource "aws_iam_organizations_features" "root_access" {
  enabled_features = [
    "RootCredentialsManagement",
    "RootSessions",
  ]

  depends_on = [aws_organizations_organization.this]
}

resource "aws_organizations_account" "workload" {
  name      = var.workload_account_name
  email     = var.workload_account_email
  parent_id = aws_organizations_organizational_unit.workload.id

  # Billing stays in the management account.
  iam_user_access_to_billing = "DENY"

  # Removing it from Terraform must never close the account; closing is a deliberate act.
  close_on_deletion = false

  # role_name is left unset, so AWS creates OrganizationAccountAccessRole, which management
  # account admins can assume. It is the way back in if Identity Center fails, and it gives
  # nobody access they don't already have through the WorkloadAdmin permission set.

  lifecycle {
    prevent_destroy = true
    # Neither can be read back from AWS, so Terraform would otherwise try to replace the account.
    ignore_changes = [role_name, iam_user_access_to_billing]
  }

  depends_on = [aws_iam_organizations_features.root_access]
}
